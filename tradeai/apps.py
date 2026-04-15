# =========================================================
# [FILE] apps.py
# [PATH] <project_root>/tradeai/apps.py
#
# このファイルは何？
# - tradeai アプリのDjango設定です。
# =========================================================

from django.apps import AppConfig


class TradeaiConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "tradeai"
    verbose_name = "Trade AI"