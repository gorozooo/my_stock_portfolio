# =========================================================
# [FILE] admin.py
# [PATH] <project_root>/tradeai/admin.py
#
# このファイルは何？
# - tradeai のモデルを Django 管理画面で見られるようにするファイルです。
# =========================================================

from django.contrib import admin

from .models import (
    DemoTrade,
    LearningResult,
    LearningSnapshot,
    NotifyLog,
    RegimeSnapshot,
    SignalEvent,
    UniverseTicker,
    WatchlistItem,
)


@admin.register(UniverseTicker)
class UniverseTickerAdmin(admin.ModelAdmin):
    list_display = ("ticker", "name", "user", "in_nikkei225", "in_topix", "from_watchlist", "from_holding", "is_active", "priority")
    list_filter = ("is_active", "in_nikkei225", "in_topix", "from_watchlist", "from_holding")
    search_fields = ("ticker", "name", "user__username")
    ordering = ("priority", "ticker")


@admin.register(WatchlistItem)
class WatchlistItemAdmin(admin.ModelAdmin):
    list_display = ("ticker", "name", "user", "long_enabled", "short_enabled", "notify_enabled", "is_active", "priority")
    list_filter = ("long_enabled", "short_enabled", "notify_enabled", "is_active")
    search_fields = ("ticker", "name", "user__username")
    ordering = ("priority", "ticker")


@admin.register(RegimeSnapshot)
class RegimeSnapshotAdmin(admin.ModelAdmin):
    list_display = ("as_of", "user", "market_bias", "long_score", "short_score")
    list_filter = ("market_bias",)
    search_fields = ("user__username", "summary_text")
    ordering = ("-as_of",)


@admin.register(SignalEvent)
class SignalEventAdmin(admin.ModelAdmin):
    list_display = ("event_at", "ticker", "name", "user", "scope", "direction", "level", "status", "score_total")
    list_filter = ("scope", "direction", "level", "status")
    search_fields = ("ticker", "name", "reason_text", "user__username")
    ordering = ("-event_at",)


@admin.register(DemoTrade)
class DemoTradeAdmin(admin.ModelAdmin):
    list_display = ("entry_at", "ticker", "name", "user", "direction", "source_scope", "status", "result_label", "entry_price", "close_price")
    list_filter = ("direction", "source_scope", "status", "result_label")
    search_fields = ("ticker", "name", "entry_reason_text", "user__username")
    ordering = ("-entry_at",)


@admin.register(LearningSnapshot)
class LearningSnapshotAdmin(admin.ModelAdmin):
    list_display = ("snapshot_at", "ticker", "name", "user", "direction", "source_scope", "signal_score", "was_notified", "was_entered")
    list_filter = ("direction", "source_scope", "was_notified", "was_entered")
    search_fields = ("ticker", "name", "user__username")
    ordering = ("-snapshot_at",)


@admin.register(LearningResult)
class LearningResultAdmin(admin.ModelAdmin):
    list_display = ("settled_at", "snapshot", "result_label", "hold_days", "pnl_yen", "pnl_pct")
    list_filter = ("result_label",)
    search_fields = ("snapshot__ticker", "exit_reason")
    ordering = ("-settled_at",)


@admin.register(NotifyLog)
class NotifyLogAdmin(admin.ModelAdmin):
    list_display = ("sent_at", "ticker", "name", "user", "direction", "level", "channel", "was_sent")
    list_filter = ("direction", "level", "channel", "was_sent")
    search_fields = ("ticker", "name", "message_text", "user__username", "dedupe_key")
    ordering = ("-sent_at",)