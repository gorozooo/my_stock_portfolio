from django.urls import path
from django.http import HttpResponse

# ← ここがポイント：viewsパッケージ内の各モジュールを明示的に import
from .views import core as core_views
from .views import settings as settings_views
from .views import api as api_views

urlpatterns = [
    path("", core_views.main, name="home"),
    # トレンド判定ページ
    path("trend/", core_views.trend_page, name="trend"),
    # API（/api/trend?ticker=...）
    path("api/trend", core_views.trend_api, name="trend_api"),
    # HTMX が差し替えるカード断片
    path("trend/card", core_views.trend_card_partial, name="trend_card_partial"),
    # ヘルスチェック
    path("healthz", lambda r: HttpResponse("ok"), name="healthz"),

    # 新API（分割版）に一本化
    path("api/metrics", api_views.metrics, name="api_metrics"),
    path("api/ohlc", api_views.ohlc, name="api_ohlc"),

    # 設定画面
    path("settings/trade", settings_views.trade_setting, name="trade_setting"),
]