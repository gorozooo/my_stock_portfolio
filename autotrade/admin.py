from django.contrib import admin
from .models import AutoTradeDailyState


@admin.register(AutoTradeDailyState)
class AutoTradeDailyStateAdmin(admin.ModelAdmin):
    list_display = ("date", "gate_level", "strategy", "equity_yen", "pnl_day_yen", "pnl_total_yen", "updated_at")
    list_filter = ("gate_level", "strategy", "date")
    search_fields = ("date",)