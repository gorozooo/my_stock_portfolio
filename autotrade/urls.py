"""
[FILE] autotrade/urls.py
[PATH] <project_root>/autotrade/urls.py

このファイルは何？
- /autotrade/ にアクセスした時に、どの画面（view）を開くかを決めるルーティングです。
"""

from django.urls import path
from . import views

app_name = "autotrade"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
]