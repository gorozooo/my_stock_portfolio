# =========================================
# [FILE] urls.py
# [PATH] kakeibo/urls.py
#
# このファイルは何？
# /kakeibo/ 配下のURLを、家計簿の各画面に接続する。
# =========================================

from django.urls import path
from .views import (
    dashboard,
    expense_create,
    income_create,
    settings_view,
    account_delete,
)

app_name = "kakeibo"

urlpatterns = [
    path("", dashboard, name="dashboard"),
    path("expense/", expense_create, name="expense_create"),
    path("income/", income_create, name="income_create"),
    path("settings/", settings_view, name="settings"),
    path("settings/account/<int:pk>/delete/", account_delete, name="account_delete"),
]