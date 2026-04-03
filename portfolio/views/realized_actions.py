# [FILE] realized_actions.py
# [PATH] portfolio/views/realized_actions.py
#
# このファイルは何？
# - 実現損益の作成/削除/クローズ送信だけを切り出したアクション系ビュー
# - 一覧/分析系は旧 realized.py に残す
#
# 今回の修正ポイント
# - 米国株クローズ時、Holding.fx_rate が Decimal でも落ちないように修正
# - 実損入力あり/なしの仕様はそのまま維持
# - close_submit の FX パースを安全化

from __future__ import annotations

from decimal import Decimal
import traceback

from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.template.loader import render_to_string
from django.utils import timezone
from django.views.decorators.http import require_POST
from django.db.models import Q

from ..models import Holding, RealizedTrade, TradeEvent
from ..models_cash import CashLedger
from ..services.trades.close_service import close_holding
from ..services.cash.ledger_service import delete_trade_event_ledgers, upsert_trade_event_ledger
from . import realized as realized_views


def _to_dec(v, default="0"):
    try:
        return Decimal(str(v if v not in (None, "") else default))
    except Exception:
        return Decimal(default)


@login_required
@require_POST
@transaction.atomic
def create(request):
    """
    手動でクローズ履歴を入れる画面用。
    方針:
    - SPEC/NISA の BUY はここでは受け付けない（保有登録から入れる）
    - SPEC/NISA の SELL は現物売却として扱う
    - MARGIN は side に応じて信用返済の履歴として扱う
    """
    date_raw = (request.POST.get("date") or "").strip()
    try:
        trade_at = timezone.datetime.fromisoformat(date_raw).date() if date_raw else timezone.localdate()
    except Exception:
        trade_at = timezone.localdate()

    ticker = (request.POST.get("ticker") or "").strip().upper()
    name = (request.POST.get("name") or "").strip()
    side = (request.POST.get("side") or "SELL").upper()
    broker = (request.POST.get("broker") or "OTHER").upper()
    account = (request.POST.get("account") or "SPEC").upper()

    qty = int(request.POST.get("qty") or 0)
    price = _to_dec(request.POST.get("price"))
    fee = _to_dec(request.POST.get("fee"))
    tax = _to_dec(request.POST.get("tax"))
    pnl_input = _to_dec(request.POST.get("pnl_input"))
    memo = (request.POST.get("memo") or "").strip()

    opened_raw = (request.POST.get("opened_at") or "").strip()
    opened_at = None
    if opened_raw:
        try:
            opened_at = timezone.datetime.fromisoformat(opened_raw).date()
        except Exception:
            opened_at = None

    sector33_code = (request.POST.get("sector33_code") or "").strip()
    sector33_name = (request.POST.get("sector33_name") or "").strip()
    country = (request.POST.get("country") or "JP").strip().upper()
    currency = (request.POST.get("currency") or "JPY").strip().upper()

    open_fx_raw = (request.POST.get("open_fx_rate") or "").strip()
    close_fx_raw = (request.POST.get("close_fx_rate") or "").strip()
    fx_rate_raw = (request.POST.get("fx_rate") or "").strip()

    def _parse_fx(raw):
        if raw in ("", None):
            return None
        v = _to_dec(raw)
        return v if v > 0 else None

    open_fx_rate = _parse_fx(open_fx_raw)
    close_fx_rate = _parse_fx(close_fx_raw) or _parse_fx(fx_rate_raw)

    strategy_label = (request.POST.get("strategy_label") or "").strip()
    policy_key = (request.POST.get("policy_key") or "").strip()
    is_ai_signal = (request.POST.get("is_ai_signal") or "").strip().lower() in ["1", "true", "on", "yes"]
    position_key = (request.POST.get("position_key") or "").strip()

    if not ticker or qty <= 0 or price <= 0:
        return JsonResponse({"ok": False, "error": "入力が不足しています"}, status=400)

    if account in ("SPEC", "NISA") and side == "BUY":
        return JsonResponse(
            {"ok": False, "error": "現物/NISA の買付は保有登録から入力してください。"},
            status=400,
        )

    basis = None
    if qty > 0:
        try:
            if side == "SELL":
                basis_calc = price - (pnl_input + fee + tax) / Decimal(qty)
                basis = basis_calc if basis_calc > 0 else None
            else:
                basis = price
        except Exception:
            basis = None

    rt = RealizedTrade.objects.create(
        user=request.user,
        trade_at=trade_at,
        opened_at=opened_at,
        side=side,
        ticker=ticker,
        name=name,
        sector33_code=sector33_code,
        sector33_name=sector33_name,
        qty=qty,
        price=price,
        basis=basis,
        fee=fee,
        tax=tax,
        broker=broker,
        account=account,
        country=country,
        currency=currency,
        open_fx_rate=open_fx_rate,
        close_fx_rate=close_fx_rate,
        fx_rate=close_fx_rate or open_fx_rate,
        cashflow=pnl_input,
        hold_days=(trade_at - opened_at).days if opened_at else None,
        strategy_label=strategy_label,
        policy_key=policy_key,
        is_ai_signal=is_ai_signal,
        position_key=position_key,
        memo=memo,
    )

    if account == "MARGIN":
        event_type = TradeEvent.EventType.MARGIN_CLOSE
        cash_amount_jpy = int(round(float(rt.pnl_jpy or 0)))
    else:
        event_type = TradeEvent.EventType.SPOT_SELL
        cash_amount_jpy = None

    ev = TradeEvent.objects.create(
        user=request.user,
        realized_trade=rt,
        trade_at=trade_at,
        opened_at=opened_at,
        event_type=event_type,
        side=side,
        ticker=ticker,
        name=name,
        sector33_code=sector33_code,
        sector33_name=sector33_name,
        qty=qty,
        price=price,
        basis=basis,
        fee=fee,
        tax=tax,
        broker=broker,
        account=account,
        country=country,
        currency=currency,
        open_fx_rate=open_fx_rate,
        close_fx_rate=close_fx_rate,
        fx_rate=close_fx_rate or open_fx_rate,
        cash_amount_jpy=cash_amount_jpy,
        position_key=position_key,
        memo=memo,
    )
    upsert_trade_event_ledger(ev)

    q = (request.POST.get("q") or "").strip()
    qs = RealizedTrade.objects.filter(user=request.user).order_by("-trade_at", "-id")
    if q:
        qs = qs.filter(Q(ticker__icontains=q) | Q(name__icontains=q))

    rows = realized_views._with_metrics(qs)
    agg = realized_views._aggregate(qs)

    table_html = render_to_string("realized/_table.html", {"trades": rows}, request=request)
    summary_html = render_to_string("realized/_summary.html", {"agg": agg}, request=request)
    return JsonResponse({"ok": True, "table": table_html, "summary": summary_html})


@login_required
@require_POST
def delete(request, pk: int):
    trade = RealizedTrade.objects.filter(pk=pk, user=request.user).first()
    if trade:
        linked_events = list(trade.trade_events.values_list("id", flat=True))
        for ev_id in linked_events:
            delete_trade_event_ledgers(ev_id)
        trade.trade_events.all().delete()
        trade.delete()

    q = (request.POST.get("q") or "").strip()
    qs = RealizedTrade.objects.filter(user=request.user).order_by("-trade_at", "-id")
    if q:
        qs = qs.filter(Q(ticker__icontains=q) | Q(name__icontains=q))

    rows = realized_views._with_metrics(qs)
    agg = realized_views._aggregate(qs)

    table_html = render_to_string("realized/_table.html", {"trades": rows}, request=request)
    summary_html = render_to_string("realized/_summary.html", {"agg": agg}, request=request)
    return JsonResponse({"ok": True, "table": table_html, "summary": summary_html})


@login_required
@require_POST
@transaction.atomic
def close_submit(request, pk: int):
    try:
        h = get_object_or_404(Holding, pk=pk, user=request.user)

        date_raw = (request.POST.get("date") or "").strip()
        try:
            trade_at = timezone.datetime.fromisoformat(date_raw).date() if date_raw else timezone.localdate()
        except Exception:
            trade_at = timezone.localdate()

        side_in = (request.POST.get("side") or "").upper()
        qty_in = int(request.POST.get("qty") or 0)
        price = _to_dec(request.POST.get("price"))
        tax_in = _to_dec(request.POST.get("tax"))

        cashflow_raw = (request.POST.get("cashflow") or "").strip()
        pnl_input = _to_dec(cashflow_raw)
        pnl_input_given = cashflow_raw != ""

        broker = (request.POST.get("broker") or h.broker or "OTHER").upper()
        account = (request.POST.get("account") or h.account or "SPEC").upper()
        memo = (request.POST.get("memo") or "").strip()
        name = (request.POST.get("name") or h.name or "").strip()

        sector33_code = (request.POST.get("sector33_code") or "").strip()
        sector33_name = (request.POST.get("sector33_name") or "").strip()
        country = (request.POST.get("country") or h.market or "JP").strip().upper()
        currency = (request.POST.get("currency") or h.currency or "JPY").strip().upper()

        def _parse_fx(raw):
            if raw in (None, ""):
                return None
            s = str(raw).replace(",", "").strip()
            if not s:
                return None
            val = _to_dec(s)
            return val if val > 0 else None

        open_fx_rate = _parse_fx(getattr(h, "fx_rate", None))
        close_fx_rate = (
            _parse_fx(request.POST.get("close_fx_rate"))
            or _parse_fx(request.POST.get("fx_rate"))
            or open_fx_rate
        )

        strategy_label = (request.POST.get("strategy_label") or "").strip()
        policy_key = (request.POST.get("policy_key") or "").strip()
        is_ai_signal = (request.POST.get("is_ai_signal") or "").strip().lower() in ["1", "true", "on", "yes"]
        position_key = (request.POST.get("position_key") or f"{h.ticker}-{(h.opened_at or trade_at).isoformat()}-{account}").strip()

        close_holding(
            holding=h,
            trade_at=trade_at,
            side_in=side_in,
            qty_in=qty_in,
            price=price,
            tax_in=tax_in,
            pnl_input=pnl_input,
            pnl_input_given=pnl_input_given,
            broker=broker,
            account=account,
            memo=memo,
            name=name,
            sector33_code=sector33_code,
            sector33_name=sector33_name,
            country=country,
            currency=currency,
            open_fx_rate=open_fx_rate,
            close_fx_rate=close_fx_rate,
            strategy_label=strategy_label,
            policy_key=policy_key,
            is_ai_signal=is_ai_signal,
            position_key=position_key,
        )

        q = (request.POST.get("q") or "").strip()
        qs = RealizedTrade.objects.filter(user=request.user).order_by("-trade_at", "-id")
        if q:
            qs = qs.filter(Q(ticker__icontains=q) | Q(name__icontains=q))

        rows = realized_views._with_metrics(qs)
        agg = realized_views._aggregate(qs)

        table_html = render_to_string("realized/_table.html", {"trades": rows}, request=request)
        summary_html = render_to_string("realized/_summary.html", {"agg": agg, "q": q}, request=request)

        if request.headers.get("HX-Request") == "true":
            return JsonResponse({"ok": True, "table": table_html, "summary": summary_html})
        return redirect("realized_list")

    except Exception as e:
        if request.headers.get("HX-Request") == "true":
            return JsonResponse(
                {"ok": False, "error": str(e), "traceback": traceback.format_exc()},
                status=400,
            )
        return redirect("realized_list")