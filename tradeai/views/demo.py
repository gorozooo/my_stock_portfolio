# =========================================================
# [FILE] demo.py
# [PATH] <project_root>/tradeai/views/demo.py
#
# このファイルは何？
# - tradeai のデモページです。
# - 候補からデモ建玉を作成し、一覧表示し、手動クローズも行います。
# - 学習へ回しやすいように、エントリー時点の情報も payload に保存します。
# =========================================================

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from tradeai.models.demo_trade import DemoTrade


def _parse_decimal(raw: str | None) -> Decimal | None:
    value = (raw or "").strip()
    if not value:
        return None
    try:
        return Decimal(value)
    except (InvalidOperation, TypeError, ValueError):
        return None


def _parse_positive_int(raw: str | None, default: int = 100) -> int:
    value = (raw or "").strip()
    if not value:
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    if parsed <= 0:
        return default
    return parsed


def _normalize_direction(raw: str | None) -> str:
    value = (raw or "").strip().upper()
    if value == DemoTrade.DirectionChoices.SHORT:
        return DemoTrade.DirectionChoices.SHORT
    return DemoTrade.DirectionChoices.LONG


def _normalize_source_scope(raw: str | None) -> str:
    value = (raw or "").strip().upper()
    if value == DemoTrade.SourceScopeChoices.HOLDING:
        return DemoTrade.SourceScopeChoices.HOLDING
    if value == DemoTrade.SourceScopeChoices.WATCHLIST:
        return DemoTrade.SourceScopeChoices.WATCHLIST
    return DemoTrade.SourceScopeChoices.UNIVERSE


def _build_entry_reason_text(decision_text: str, fact_reasons: list[str]) -> str:
    lines: list[str] = []

    if decision_text:
        lines.append(decision_text)

    if fact_reasons:
        lines.append("根拠: " + " / ".join(fact_reasons[:4]))

    return "\n".join(lines).strip()


def _calc_pnl(
    direction: str,
    entry_price: Decimal,
    close_price: Decimal,
    qty: int,
) -> tuple[Decimal, Decimal]:
    qty_dec = Decimal(qty)

    if direction == DemoTrade.DirectionChoices.SHORT:
        pnl_yen = (entry_price - close_price) * qty_dec
        pnl_pct = ((entry_price - close_price) / entry_price) * Decimal("100") if entry_price > 0 else Decimal("0")
    else:
        pnl_yen = (close_price - entry_price) * qty_dec
        pnl_pct = ((close_price - entry_price) / entry_price) * Decimal("100") if entry_price > 0 else Decimal("0")

    pnl_yen = pnl_yen.quantize(Decimal("0.01"))
    pnl_pct = pnl_pct.quantize(Decimal("0.01"))
    return pnl_yen, pnl_pct


@login_required
def demo_page(request):
    user = request.user

    open_trades = list(
        DemoTrade.objects.filter(
            user=user,
            status=DemoTrade.StatusChoices.OPEN,
        ).order_by("-entry_at", "-id")
    )

    recent_closed_trades = list(
        DemoTrade.objects.filter(
            user=user,
            status=DemoTrade.StatusChoices.CLOSED,
        ).order_by("-close_at", "-id")[:20]
    )

    open_demo_count = len(open_trades)
    closed_demo_count = DemoTrade.objects.filter(
        user=user,
        status=DemoTrade.StatusChoices.CLOSED,
    ).count()
    total_demo_count = DemoTrade.objects.filter(user=user).count()

    closed_sum = (
        DemoTrade.objects.filter(
            user=user,
            status=DemoTrade.StatusChoices.CLOSED,
        ).aggregate(total=Sum("pnl_yen"))
    )
    total_closed_pnl_yen = closed_sum.get("total") or Decimal("0")

    win_count = DemoTrade.objects.filter(
        user=user,
        status=DemoTrade.StatusChoices.CLOSED,
        result_label=DemoTrade.ResultLabelChoices.WIN,
    ).count()
    loss_count = DemoTrade.objects.filter(
        user=user,
        status=DemoTrade.StatusChoices.CLOSED,
        result_label=DemoTrade.ResultLabelChoices.LOSS,
    ).count()
    flat_count = DemoTrade.objects.filter(
        user=user,
        status=DemoTrade.StatusChoices.CLOSED,
        result_label=DemoTrade.ResultLabelChoices.FLAT,
    ).count()

    judged_count = win_count + loss_count + flat_count
    win_rate_pct = round((win_count / judged_count) * 100, 2) if judged_count > 0 else 0

    context = {
        "page_title": "デモ",
        "open_demo_count": open_demo_count,
        "closed_demo_count": closed_demo_count,
        "total_demo_count": total_demo_count,
        "total_closed_pnl_yen": total_closed_pnl_yen,
        "win_count": win_count,
        "loss_count": loss_count,
        "flat_count": flat_count,
        "win_rate_pct": win_rate_pct,
        "open_trades": open_trades,
        "recent_closed_trades": recent_closed_trades,
    }
    return render(request, "tradeai/demo.html", context)


@login_required
def demo_create_from_candidate(request):
    if request.method != "POST":
        return redirect("tradeai_demo")

    user = request.user

    ticker = (request.POST.get("ticker") or "").strip().upper()
    name = (request.POST.get("name") or "").strip()
    direction = _normalize_direction(request.POST.get("direction"))
    source_scope = _normalize_source_scope(request.POST.get("source_scope"))
    sector_name = (request.POST.get("sector_name") or "").strip()
    action_label = (request.POST.get("action_label") or "").strip()
    regime_relation_label = (request.POST.get("regime_relation_label") or "").strip()
    decision_text = (request.POST.get("decision_text") or "").strip()

    entry_price = _parse_decimal(request.POST.get("entry_price"))
    stop_price = _parse_decimal(request.POST.get("sl_price"))
    take_profit_price = _parse_decimal(request.POST.get("tp_price"))
    qty = _parse_positive_int(request.POST.get("qty"), default=100)

    fact_reasons = [x.strip() for x in request.POST.getlist("fact_reason") if (x or "").strip()]
    indicator_pills = [x.strip() for x in request.POST.getlist("indicator_pill") if (x or "").strip()]

    score_100_raw = (request.POST.get("score_100") or "").strip()
    score_100 = None
    try:
        if score_100_raw:
            score_100 = int(score_100_raw)
    except (TypeError, ValueError):
        score_100 = None

    if not ticker:
        messages.error(request, "デモ登録に失敗しました。証券コードがありません。")
        return redirect("tradeai_candidates")

    if entry_price is None or entry_price <= 0:
        messages.error(request, f"{ticker} のデモ登録に失敗しました。Entry価格が不正です。")
        return redirect("tradeai_candidates")

    already_open = DemoTrade.objects.filter(
        user=user,
        ticker=ticker,
        direction=direction,
        status=DemoTrade.StatusChoices.OPEN,
    ).exists()

    if already_open:
        messages.warning(request, f"{ticker} は同じ方向のデモ建玉がすでにあります。")
        return redirect("tradeai_demo")

    entry_payload = {
        "sector_name": sector_name,
        "score_100": score_100,
        "action_label": action_label,
        "regime_relation_label": regime_relation_label,
        "fact_reasons": fact_reasons,
        "indicator_pills": indicator_pills,
        "created_from": "candidate_snapshot",
    }

    trade = DemoTrade.objects.create(
        user=user,
        ticker=ticker,
        name=name,
        direction=direction,
        source_scope=source_scope,
        status=DemoTrade.StatusChoices.OPEN,
        result_label=DemoTrade.ResultLabelChoices.UNKNOWN,
        entry_at=timezone.now(),
        entry_price=entry_price,
        stop_price=stop_price,
        take_profit_price=take_profit_price,
        qty=qty,
        entry_reason_text=_build_entry_reason_text(decision_text, fact_reasons),
        entry_payload=entry_payload,
    )

    direction_label = trade.get_direction_display()
    messages.success(request, f"{ticker} をデモ建玉に追加しました。({direction_label})")
    return redirect("tradeai_demo")


@login_required
def demo_close(request, pk: int):
    if request.method != "POST":
        return redirect("tradeai_demo")

    trade = get_object_or_404(DemoTrade, pk=pk, user=request.user)

    if trade.status != DemoTrade.StatusChoices.OPEN:
        messages.warning(request, f"{trade.ticker} はすでにクローズ済みです。")
        return redirect("tradeai_demo")

    close_price = _parse_decimal(request.POST.get("close_price"))
    exit_reason = (request.POST.get("exit_reason") or "").strip()

    if close_price is None or close_price <= 0:
        messages.error(request, f"{trade.ticker} のクローズに失敗しました。終了価格を入れてください。")
        return redirect("tradeai_demo")

    pnl_yen, pnl_pct = _calc_pnl(
        direction=trade.direction,
        entry_price=trade.entry_price,
        close_price=close_price,
        qty=trade.qty,
    )

    if pnl_yen > 0:
        result_label = DemoTrade.ResultLabelChoices.WIN
    elif pnl_yen < 0:
        result_label = DemoTrade.ResultLabelChoices.LOSS
    else:
        result_label = DemoTrade.ResultLabelChoices.FLAT

    trade.close_at = timezone.now()
    trade.close_price = close_price
    trade.pnl_yen = pnl_yen
    trade.pnl_pct = pnl_pct
    trade.result_label = result_label
    trade.status = DemoTrade.StatusChoices.CLOSED
    trade.exit_reason = exit_reason
    trade.save()

    messages.success(
        request,
        f"{trade.ticker} をクローズしました。損益 {trade.pnl_yen} 円 / {trade.pnl_pct}%",
    )
    return redirect("tradeai_demo")