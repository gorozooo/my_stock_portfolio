# =========================================
# [FILE] urls.py
# [PATH] kakeibo/urls.py
#
# このファイルは何？
# /kakeibo/ 配下のURLを、家計簿の各画面に接続する（B案：月次方式）。
# - 今回：/kakeibo/settings/ は「設定メニュー」に変更
# - 既存の設定編集画面は /kakeibo/settings/edit/ に移動
# - 管理画面を /kakeibo/manage/ 配下に追加
# =========================================

from django.urls import path
from .views import (
    dashboard,
    income,
    expense,
    expense_fixed,
    expense_variable,
    bank,
    # settings
    settings_menu,
    settings_view,
    # manage
    manage_menu,
    manage_income,
    manage_variable,
    manage_fixed,
    manage_bank,
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

    # 設定：まずメニューを出す
    path("settings/", settings_menu, name="settings_menu"),
    # 設定：編集（従来のタブ切替画面）
    path("settings/edit/", settings_view, name="settings"),

    # 管理：探して編集/削除する専用
    path("manage/", manage_menu, name="manage_menu"),
    path("manage/income/", manage_income, name="manage_income"),
    path("manage/variable/", manage_variable, name="manage_variable"),
    path("manage/fixed/", manage_fixed, name="manage_fixed"),
    path("manage/bank/", manage_bank, name="manage_bank"),
]