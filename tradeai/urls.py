# =========================================================
# [FILE] urls.py
# [PATH] <project_root>/tradeai/urls.py
#
# このファイルは何？
# - tradeai アプリのURL設定です。
# - ダッシュボード導線として、候補抽出ページとデモページも追加しています。
# =========================================================

from django.urls import path

from .views.api import api_ticker_name
from .views.candidates import candidates_page
from .views.dashboard import dashboard
from .views.demo import demo_page
from .views.holdings import holdings_page
from .views.watch_signals import watch_signals_page
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
    path("api/ticker-name/", api_ticker_name, name="tradeai_api_ticker_name"),
    path("holdings/", holdings_page, name="tradeai_holdings"),
    path("watch-signals/", watch_signals_page, name="tradeai_watch_signals"),
    path("candidates/", candidates_page, name="tradeai_candidates"),
    path("demo/", demo_page, name="tradeai_demo"),
]