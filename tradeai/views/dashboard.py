# =========================================================
# [FILE] dashboard.py
# [PATH] <project_root>/tradeai/views/dashboard.py
#
# このファイルは何？
# - tradeai のトップ画面を表示する view です。
# - ダッシュボードの導線整理と、最新の地合いの自動表示を担当します。
# - 今回はデモ建玉プレビューも表示します。
# =========================================================

from datetime import timedelta

from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.utils import timezone

from portfolio.models import Holding
from tradeai.models.demo_trade import DemoTrade
from tradeai.models.signal_event import SignalEvent
from tradeai.models.universe import UniverseTicker
from tradeai.models.watchlist import WatchlistItem
from tradeai.services.holdings.monitor_service import build_holding_monitor_rows
from tradeai.services.regime.regime_service import ensure_recent_regime_snapshot
from tradeai.services.watchlist.monitor_service import build_watch_signal_rows


@login_required
def dashboard(request):
    user = request.user
    now = timezone.now()
    recent_from = now - timedelta(days=7)

    recent_watchlist = WatchlistItem.objects.filter(user=user).order_by("priority", "ticker")[:6]
    active_universe = UniverseTicker.objects.filter(user=user, is_active=True).order_by("priority", "ticker")[:8]

    holding_rows = build_holding_monitor_rows(user)
    holding_preview = holding_rows[:4]
    holding_alert_count = sum(1 for row in holding_rows if row["level"] in ("ATTENTION", "STRONG"))

    watch_signal_rows = build_watch_signal_rows(user)
    watch_signal_preview = [row for row in watch_signal_rows if row["level"] in ("STRONG", "ATTENTION")][:4]
    watch_alert_count = sum(1 for row in watch_signal_rows if row["level"] in ("ATTENTION", "STRONG"))

    last_regime = ensure_recent_regime_snapshot(user=user, max_age_minutes=90)

    regime_long_reasons = []
    regime_short_reasons = []
    regime_components = []
    if last_regime and isinstance(last_regime.payload, dict):
        regime_long_reasons = list(last_regime.payload.get("long_reasons") or [])
        regime_short_reasons = list(last_regime.payload.get("short_reasons") or [])
        regime_components = list(last_regime.payload.get("components") or [])

    progress_items = [
        {"label": "保有監視", "state": "done"},
        {"label": "ウォッチ監視", "state": "done"},
        {"label": "地合い", "state": "done" if last_regime else "todo"},
        {"label": "候補抽出", "state": "done"},
        {"label": "デモ", "state": "done"},
        {"label": "学習", "state": "prep"},
    ]

    open_demo_qs = DemoTrade.objects.filter(
        user=user,
        status=DemoTrade.StatusChoices.OPEN,
    ).order_by("-entry_at", "-id")

    open_demo_preview = list(open_demo_qs[:4])

    context = {
        "page_title": "TradeAI",
        "universe_count": UniverseTicker.objects.filter(user=user, is_active=True).count(),
        "watchlist_count": WatchlistItem.objects.filter(user=user, is_active=True).count(),
        "holding_count": Holding.objects.filter(user=user, quantity__gt=0).count(),
        "open_demo_count": open_demo_qs.count(),
        "recent_signal_count": SignalEvent.objects.filter(
            user=user,
            event_at__gte=recent_from,
        ).count(),
        "last_regime": last_regime,
        "regime_long_reasons": regime_long_reasons,
        "regime_short_reasons": regime_short_reasons,
        "regime_components": regime_components,
        "recent_watchlist": recent_watchlist,
        "active_universe": active_universe,
        "holding_preview": holding_preview,
        "holding_alert_count": holding_alert_count,
        "watch_signal_preview": watch_signal_preview,
        "watch_alert_count": watch_alert_count,
        "progress_items": progress_items,
        "open_demo_preview": open_demo_preview,
    }
    return render(request, "tradeai/dashboard.html", context)