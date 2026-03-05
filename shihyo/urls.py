"""
[FILE] urls.py
[PATH] <project_root>/shihyo/urls.py

このファイルは何？
- 指標専用アプリのURLルーティングです。
- /shihyo/ でダッシュボードを表示します。
"""

from django.urls import path
from shihyo import views

app_name = "shihyo"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
]