# =========================================================
# [FILE] urls.py
# [PATH] <project_root>/tradeai/urls.py
#
# このファイルは何？
# - tradeai アプリのURL設定です。
# - 今回はダッシュボードとウォッチリストをつなぎます。
# =========================================================

from django.urls import path

from .views.dashboard import dashboard
from .views.watchlist import (
    watchlist_delete,
    watchlist_page,
    watchlist_toggle_active,
)

urlpatterns = [
    path("", dashboard, name="tradeai_dashboard"),
    path("watchlist/", watchlist_page, name="tradeai_watchlist"),
    path("watchlist/<int:pk>/toggle/", watchlist_toggle_active, name="tradeai_watchlist_toggle"),
    path("watchlist/<int:pk>/delete/", watchlist_delete, name="tradeai_watchlist_delete"),
]