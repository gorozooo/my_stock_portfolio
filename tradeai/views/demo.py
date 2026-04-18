# =========================================================
# [FILE] demo.py
# [PATH] <project_root>/tradeai/views/demo.py
#
# このファイルは何？
# - tradeai のデモページです。
# - 自動デモ建玉の一覧・履歴・成績を表示します。
# =========================================================

from __future__ import annotations

from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.db.models import Sum
from django.shortcuts import render

from tradeai.models.demo_trade import DemoTrade


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
    }
    return render(request, "tradeai/demo.html", context)