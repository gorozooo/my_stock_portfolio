from django.urls import path
from django.http import HttpResponse
from . import views

urlpatterns = [
    path("", views.main, name="home"),
    # トレンド判定ページ（フォームのある画面）
    path("trend/", views.trend_page, name="trend"),
    # API（/api/trend?ticker=...）
    path("api/trend", views.trend_api, name="trend_api"),
    # HTMX が差し替えるカード断片
    path("trend/card", views.trend_card_partial, name="trend_card_partial"),
    # ヘルスチェック
    path("healthz", lambda r: HttpResponse("ok"), name="healthz"),
    
    path("api/ohlc", views.ohlc_api, name="ohlc_api"),
    path("api/metrics", views.metrics_api, name="metrics_api"),
]