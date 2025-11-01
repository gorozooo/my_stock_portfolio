from django.contrib import admin
from .models import ActionLog, Reminder, WatchEntry
from .models_order import OrderMemo

@admin.register(ActionLog)
class ActionLogAdmin(admin.ModelAdmin):
    """操作ログ"""
    list_display = ("created_at", "user", "ticker", "action")
    list_filter = ("action",)
    search_fields = ("ticker", "note")


@admin.register(Reminder)
class ReminderAdmin(admin.ModelAdmin):
    """リマインダー"""
    # ✅ 'done' を削除（モデルに存在しないため）
    list_display = ("fire_at", "user", "ticker", "message")
    list_filter = ("fire_at",)
    search_fields = ("ticker", "message")


@admin.register(WatchEntry)
class WatchEntryAdmin(admin.ModelAdmin):
    """ウォッチリスト"""
    list_display = ("updated_at", "user", "ticker", "name", "status", "in_position")
    list_filter = ("status", "in_position")
    search_fields = ("ticker", "name", "note")

@admin.register(OrderMemo)
class OrderMemoAdmin(admin.ModelAdmin):
    list_display = ("created_at", "user", "ticker", "name", "entry_price", "tp_price", "sl_price", "window")
    list_filter = ("window", "source")
    search_fields = ("ticker", "name", "policies_line")


from .admin_policy import *
