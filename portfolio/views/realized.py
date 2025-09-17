# portfolio/views/realized.py
from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.shortcuts import render, get_object_or_404, redirect
from django.http import JsonResponse, HttpResponseBadRequest, HttpResponse
from django.views.decorators.http import require_GET, require_POST
from django.utils import timezone
from django.utils.encoding import smart_str
from django.template.loader import render_to_string

from django.db.models import (
    Sum, Count, F, FloatField, Case, When, Value, Q, ExpressionWrapper
)
from django.db.models.functions import Coalesce

from ..models import RealizedTrade


# --------- ヘルパ：サイン付き金額（SELL=+、それ以外=-）を注釈 ----------
def _signed_amount_expr() -> ExpressionWrapper:
    # SELL:  qty*price - fee - tax
    # BUY他: -(qty*price) - fee - tax
    expr = Case(
        When(side="SELL", then=F("qty") * F("price") - F("fee") - F("tax")),
        default=-(F("qty") * F("price")) - F("fee") - F("tax"),
        output_field=FloatField(),
    )
    return ExpressionWrapper(expr, output_field=FloatField())


def _base_qs(request, q: str = ""):
    qs = (
        RealizedTrade.objects
        .filter(user=request.user)  # ← ユーザーで絞る
        .order_by("-trade_at", "-id")
    )
    if q:
        qs = qs.filter(Q(ticker__icontains=q) | Q(memo__icontains=q))
    # 各行にも pnl を注釈（テンプレで t.pnl を使えるように）
    return qs.annotate(pnl=_signed_amount_expr())


def _aggregate(qs):
    return qs.aggregate(
        n   = Coalesce(Count("id"), 0),
        qty = Coalesce(Sum(F("qty")), 0),           # ← 'qty' ではなく F("qty")
        fee = Coalesce(Sum(F("fee")), 0.0),         # ← 同上
        tax = Coalesce(Sum(F("tax")), 0.0),         # ← 同上
        pnl = Coalesce(Sum(_signed_amount_expr()), 0.0),
    )


# --------- 一覧ページ（フル） ----------
@login_required
@require_GET
def list_page(request):
    q = (request.GET.get("q") or "").strip()
    qs = _base_qs(request, q)
    agg = _aggregate(qs)
    return render(request, "realized/list.html", {"q": q, "trades": qs, "agg": agg})


# --------- 作成（HTMX 返却：テーブル + サマリー HTML） ----------
@login_required
@require_POST
def create(request):
    # 受け入れ名：trade_at（なければ date も受ける）
    trade_at_raw = (request.POST.get("trade_at") or request.POST.get("date") or "").strip()
    if trade_at_raw:
        try:
            if "T" in trade_at_raw:
                trade_at = timezone.make_aware(timezone.datetime.fromisoformat(trade_at_raw))
            else:
                y, m, d = map(int, trade_at_raw.split("-"))
                trade_at = timezone.make_aware(timezone.datetime(y, m, d, 0, 0, 0))
        except Exception:
            trade_at = timezone.now()
    else:
        trade_at = timezone.now()

    side   = (request.POST.get("side") or "SELL").strip().upper()
    ticker = (request.POST.get("ticker") or "").strip().upper()
    try:
        qty   = int(request.POST.get("qty") or 0)
        price = float(request.POST.get("price") or 0)
        fee   = float(request.POST.get("fee") or 0)
        tax   = float(request.POST.get("tax") or 0)
    except Exception:
        return JsonResponse({"ok": False, "error": "数値の形式が不正です"}, status=400)
    memo   = (request.POST.get("memo") or "").strip()

    if not ticker or qty <= 0 or price <= 0:
        return JsonResponse({"ok": False, "error": "入力が不足しています"}, status=400)

    RealizedTrade.objects.create(
        user=request.user,
        trade_at=trade_at,
        side=side,
        ticker=ticker,
        qty=qty,
        price=price,
        fee=fee,
        tax=tax,
        memo=memo,
    )

    # 追加後に断片を返す
    q = (request.POST.get("q") or "").strip()
    qs = _base_qs(request, q)
    agg = _aggregate(qs)

    html_table = render_to_string("realized/_table.html", {"trades": qs}, request=request)
    html_summary = render_to_string("realized/_summary.html", {"agg": agg}, request=request)
    return JsonResponse({"ok": True, "table": html_table, "summary": html_summary})


# --------- 削除（HTMX 返却：テーブル + サマリー HTML） ----------
@login_required
@require_POST
def delete(request, pk: int):
    get_object_or_404(RealizedTrade, pk=pk, user=request.user).delete()

    q = (request.POST.get("q") or "").strip()
    qs = _base_qs(request, q)
    agg = _aggregate(qs)

    html_table = render_to_string("realized/_table.html", {"trades": qs}, request=request)
    html_summary = render_to_string("realized/_summary.html", {"agg": agg}, request=request)
    return JsonResponse({"ok": True, "table": html_table, "summary": html_summary})


# --------- CSV エクスポート ----------
@login_required
@require_GET
def export_csv(request):
    q = (request.GET.get("q") or "").strip()
    qs = _base_qs(request, q)  # pnl 注釈済み

    resp = HttpResponse(content_type="text/csv; charset=utf-8")
    resp["Content-Disposition"] = 'attachment; filename="realized_trades.csv"'
    w = csv.writer(resp)
    w.writerow(["trade_at", "ticker", "side", "qty", "price", "fee", "tax", "pnl", "memo"])
    for t in qs:
        w.writerow([
            t.trade_at.date().isoformat(),
            t.ticker, t.side, t.qty, t.price, t.fee, t.tax,
            f"{t.pnl:.2f}",
            smart_str(t.memo or ""),
        ])
    return resp


# --------- 部分テンプレ（テーブル） ----------
@login_required
@require_GET
def table_partial(request):
    q = (request.GET.get("q") or "").strip()
    qs = _base_qs(request, q)
    return render(request, "realized/_table.html", {"trades": qs, "q": q})


# --------- 部分テンプレ（サマリー） ----------
@login_required
@require_GET
def summary_partial(request):
    q = (request.GET.get("q") or "").strip()
    qs = _base_qs(request, q)
    agg = _aggregate(qs)
    return render(request, "realized/_summary.html", {"agg": agg, "q": q})