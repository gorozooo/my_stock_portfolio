# portfolio/urls.py
from django.urls import path
from django.http import HttpResponse
from . import views

urlpatterns = [
    # ルートはダッシュボード(main)に戻す
    path("", views.main, name="home"),

    # トレンド判定ページ（フォームのある画面）
    path("trend/", views.trend_page, name="trend"),

    # API（/api/trend?ticker=...）
    path("api/trend", views.trend_api, name="trend_api"),

    # HTMX が差し替えるカード断片
    path("trend/card", views.trend_card_partial, name="trend_card_partial"),

    # ヘルスチェック
    path("healthz", lambda r: HttpResponse("ok"), name="healthz"),
]
