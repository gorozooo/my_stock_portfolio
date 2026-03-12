"""
[FILE] urls.py
[PATH] <project_root>/shihyo/urls.py

このファイルは何？
- 指標専用アプリのURLルーティングです。
- /shihyo/ でダッシュボードを表示します。
- /shihyo/weekly-review/ で週次レビュー専用ページを表示します。
"""

from django.urls import path

from shihyo import views
from shihyo import weekly_review_views

app_name = "shihyo"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("weekly-review/", weekly_review_views.weekly_review, name="weekly_review"),
]