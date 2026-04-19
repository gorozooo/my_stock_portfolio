# =========================================================
# [FILE] demo.py
# [PATH] <project_root>/tradeai/views/demo.py
#
# このファイルは何？
# - tradeai のデモページです。
# - 自動デモ建玉の一覧・履歴・成績に加えて、
#   LearningSnapshot / LearningResult の学習状況も表示します。
# - 今回は「デモ結果」と「学習の蓄積」を1画面で確認できるようにします。
# =========================================================

from __future__ import annotations

from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.db.models import Sum
from django.shortcuts import render

from tradeai.models.demo_trade import DemoTrade
from tradeai.models.learning_result import LearningResult
from tradeai.models.learning_snapshot import LearningSnapshot


@login_required
def demo_page(request):
    user = request.user

    open_trades = list(
        DemoTrade.objects.filter(
            user=user,
            status=DemoTrade.StatusChoices.OPEN,
        ).order_by("-entry_at", "-id")[:20]
    )

    closed_qs = DemoTrade.objects.filter(
        user=user,
        status=DemoTrade.StatusChoices.CLOSED,
    )

    recent_closed_trades = list(
        closed_qs.order_by("-close_at", "-id")[:20]
    )

    open_demo_count = len(open_trades)
    total_demo_count = DemoTrade.objects.filter(user=user).count()
    closed_demo_count = closed_qs.count()

    win_count = closed_qs.filter(result_label=DemoTrade.ResultLabelChoices.WIN).count()
    loss_count = closed_qs.filter(result_label=DemoTrade.ResultLabelChoices.LOSS).count()
    flat_count = closed_qs.filter(result_label=DemoTrade.ResultLabelChoices.FLAT).count()

    total_pnl = closed_qs.aggregate(total=Sum("pnl_yen")).get("total") or Decimal("0")
    win_rate = round((win_count / closed_demo_count) * 100.0, 1) if closed_demo_count else None

    learning_snapshots_qs = LearningSnapshot.objects.filter(user=user)
    learning_results_qs = LearningResult.objects.filter(snapshot__user=user)

    learning_snapshot_count = learning_snapshots_qs.count()
    learning_result_count = learning_results_qs.count()

    learning_win_count = learning_results_qs.filter(
        result_label=LearningResult.ResultLabelChoices.WIN
    ).count()
    learning_loss_count = learning_results_qs.filter(
        result_label=LearningResult.ResultLabelChoices.LOSS
    ).count()
    learning_flat_count = learning_results_qs.filter(
        result_label=LearningResult.ResultLabelChoices.FLAT
    ).count()

    learning_total_pnl = learning_results_qs.aggregate(total=Sum("pnl_yen")).get("total") or Decimal("0")
    learning_win_rate = (
        round((learning_win_count / learning_result_count) * 100.0, 1)
        if learning_result_count
        else None
    )

    open_linked_snapshot_count = (
        learning_snapshots_qs.filter(demo_trade__status=DemoTrade.StatusChoices.OPEN)
        .values("demo_trade_id")
        .distinct()
        .count()
    )

    closed_linked_result_count = (
        learning_results_qs.filter(snapshot__demo_trade__status=DemoTrade.StatusChoices.CLOSED)
        .values("snapshot__demo_trade_id")
        .distinct()
        .count()
    )

    pending_learning_result_count = max(closed_demo_count - closed_linked_result_count, 0)

    recent_learning_results = list(
        learning_results_qs.select_related("snapshot", "snapshot__demo_trade")
        .order_by("-settled_at", "-id")[:20]
    )

    open_trade_ids = [trade.id for trade in open_trades]
    open_snapshot_trade_ids = set(
        learning_snapshots_qs.filter(demo_trade_id__in=open_trade_ids)
        .values_list("demo_trade_id", flat=True)
        .distinct()
    )

    for trade in open_trades:
        trade.has_learning_snapshot = trade.id in open_snapshot_trade_ids

    closed_trade_ids = [trade.id for trade in recent_closed_trades]
    closed_result_trade_ids = set(
        learning_results_qs.filter(snapshot__demo_trade_id__in=closed_trade_ids)
        .values_list("snapshot__demo_trade_id", flat=True)
        .distinct()
    )

    for trade in recent_closed_trades:
        trade.has_learning_result = trade.id in closed_result_trade_ids

    context = {
        "page_title": "デモ",
        "open_demo_count": open_demo_count,
        "total_demo_count": total_demo_count,
        "closed_demo_count": closed_demo_count,
        "win_count": win_count,
        "loss_count": loss_count,
        "flat_count": flat_count,
        "win_rate": win_rate,
        "total_pnl": total_pnl,
        "open_trades": open_trades,
        "recent_closed_trades": recent_closed_trades,
        "learning_snapshot_count": learning_snapshot_count,
        "learning_result_count": learning_result_count,
        "learning_win_count": learning_win_count,
        "learning_loss_count": learning_loss_count,
        "learning_flat_count": learning_flat_count,
        "learning_total_pnl": learning_total_pnl,
        "learning_win_rate": learning_win_rate,
        "open_linked_snapshot_count": open_linked_snapshot_count,
        "closed_linked_result_count": closed_linked_result_count,
        "pending_learning_result_count": pending_learning_result_count,
        "recent_learning_results": recent_learning_results,
    }
    return render(request, "tradeai/demo.html", context)