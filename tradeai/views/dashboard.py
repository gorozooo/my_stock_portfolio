# =========================================================
# [FILE] dashboard.py
# [PATH] <project_root>/tradeai/views/dashboard.py
#
# このファイルは何？
# - tradeai のトップ画面を表示する view です。
# - 今回は「土台がつながったか確認するための画面」です。
# =========================================================

from datetime import timedelta

from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.utils import timezone

from portfolio.models import Holding
from tradeai.models.demo_trade import DemoTrade
from tradeai.models.regime_snapshot import RegimeSnapshot
from tradeai.models.signal_event import SignalEvent
from tradeai.models.universe import UniverseTicker
from tradeai.models.watchlist import WatchlistItem


@login_required
def dashboard(request):
    user = request.user
    now = timezone.now()
    recent_from = now - timedelta(days=7)

    context = {
        "page_title": "TradeAI",
        "universe_count": UniverseTicker.objects.filter(user=user, is_active=True).count(),
        "watchlist_count": WatchlistItem.objects.filter(user=user, is_active=True).count(),
        "holding_count": Holding.objects.filter(user=user, quantity__gt=0).count(),
        "open_demo_count": DemoTrade.objects.filter(
            user=user,
            status=DemoTrade.StatusChoices.OPEN,
        ).count(),
        "recent_signal_count": SignalEvent.objects.filter(
            user=user,
            event_at__gte=recent_from,
        ).count(),
        "last_regime": RegimeSnapshot.objects.filter(user=user).order_by("-as_of").first(),
    }
    return render(request, "tradeai/dashboard.html", context)