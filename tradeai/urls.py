# =========================================================
# [FILE] urls.py
# [PATH] <project_root>/tradeai/urls.py
#
# このファイルは何？
# - tradeai アプリのURL設定です。
# =========================================================

from django.urls import path

from .views.dashboard import dashboard

urlpatterns = [
    path("", dashboard, name="tradeai_dashboard"),
]