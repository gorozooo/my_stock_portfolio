# =========================================
# [FILE] urls.py
# [PATH] kakeibo/urls.py
#
# このファイルは何？
# /kakeibo/ 配下のURLを、家計簿の各画面に接続する（B案：月次方式）。
# 追加：
# - ownerで絞ったカード/口座の候補を返すAPI
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

from .views.api import api_cards, api_accounts

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

    # --- API（ownerで候補を絞る） ---
    path("api/cards/", api_cards, name="api_cards"),
    path("api/accounts/", api_accounts, name="api_accounts"),
]