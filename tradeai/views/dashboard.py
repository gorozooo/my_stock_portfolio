# =========================================================
# [FILE] dashboard.py
# [PATH] <project_root>/tradeai/views/dashboard.py
#
# このファイルは何？
# - tradeai のトップ画面を表示する view です。
# - 今回は保有監視プレビューに GC/DC と ATR の情報も載せます。
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
from tradeai.services.holdings.monitor_service import build_holding_monitor_rows


@login_required
def dashboard(request):
    user = request.user
    now = timezone.now()
    recent_from = now - timedelta(days=7)

    recent_watchlist = WatchlistItem.objects.filter(user=user).order_by("priority", "ticker")[:6]
    active_universe = UniverseTicker.objects.filter(user=user, is_active=True).order_by("priority", "ticker")[:8]

    holding_rows = build_holding_monitor_rows(user)
    holding_preview = holding_rows[:6]
    holding_alert_count = sum(1 for row in holding_rows if row["level"] in ("ATTENTION", "STRONG"))

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
        "recent_watchlist": recent_watchlist,
        "active_universe": active_universe,
        "holding_preview": holding_preview,
        "holding_alert_count": holding_alert_count,
    }
    return render(request, "tradeai/dashboard.html", context)