# portfolio/urls.py
from django.urls import path
from django.http import HttpResponse
from . import views

urlpatterns = [
    path("", views.main, name="home"),
    path("trend/", views.trend_page, name="trend"),# トレンド判定ページ（フォームのある画面）
    path("api/trend", views.trend_api, name="trend_api"),# API（/api/trend?ticker=...）
    path("trend/card", views.trend_card_partial, name="trend_card_partial"),# HTMX が差し替えるカード断片
    path("healthz", lambda r: HttpResponse("ok"), name="healthz"),# ヘルスチェック
    path("api/suggest", views.suggest_api, name="suggest_api"),
]
