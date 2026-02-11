# =========================================
# [FILE] urls.py
# [PATH] kakeibo/urls.py
#
# このファイルは何？
# /kakeibo/ へのアクセスをdashboardに接続する。
# =========================================

from django.urls import path
from .views import dashboard

app_name = "kakeibo"

urlpatterns = [
    path("", dashboard, name="dashboard"),
]