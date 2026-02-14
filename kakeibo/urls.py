# =========================================
# [FILE] urls.py
# [PATH] kakeibo/urls.py
#
# このファイルは何？
# /kakeibo/ 配下のURLを、家計簿の各画面に接続する（B案：月次方式）。
# =========================================

from django.urls import path
from .views import (
    dashboard,
    income,
    expense,
    expense_fixed,
    expense_variable,
    bank,
    settings_view,
)

app_name = "kakeibo"

urlpatterns = [
    path("", dashboard, name="dashboard"),

    # 収入（月次）
    path("income/", income, name="income"),

    # 支出：入口（固定費/変動費）
    path("expense/", expense, name="expense"),
    path("expense/fixed/", expense_fixed, name="expense_fixed"),
    path("expense/variable/", expense_variable, name="expense_variable"),

    # 銀行（月次残高）
    path("bank/", bank, name="bank"),

    # 設定（カテゴリ/口座/カード）
    path("settings/", settings_view, name="settings"),
]