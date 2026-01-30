"""
[FILE] autotrade/admin.py
[PATH] <project_root>/autotrade/admin.py

このファイルは何？
- Django管理画面（/admin/）で AutoTradeDailyState を見やすく表示する設定です。

初心者ポイント：
- ダッシュボードに何か変な値が入った時に、管理画面から確認できます。
"""

from django.contrib import admin
from .models import AutoTradeDailyState


@admin.register(AutoTradeDailyState)
class AutoTradeDailyStateAdmin(admin.ModelAdmin):
    list_display = ("date", "gate_level", "strategy", "equity_yen", "pnl_day_yen", "pnl_total_yen", "updated_at")
    list_filter = ("gate_level", "strategy", "date")
    search_fields = ("date",)