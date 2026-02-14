# =========================================
# [FILE] dashboard.py
# [PATH] kakeibo/views/dashboard.py
#
# このファイルは何？
# 家計簿トップ画面（/kakeibo/）を表示するView。
# - Transaction方式は廃止済み（B案：月次方式）
# - いまは「月次モデルで集計する前の暫定ダッシュボード」
#   → django check を通し、次の画面実装へ進むための土台
# =========================================

from datetime import date

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import render

from .permissions import kakeibo_access_required
from ..models import MonthlyIncome, MonthlyVariableExpense, FixedExpenseTemplate, BankBalance


def _month_first(d: date) -> date:
    return d.replace(day=1)


@login_required
def dashboard(request):
    if not kakeibo_access_required(request.user):
        raise PermissionDenied("You do not have access to kakeibo.")

    today = date.today()
    month = _month_first(today)

    # ✅ 収入（今月）
    income_total = (
        MonthlyIncome.objects
        .filter(month=month)
        .values_list("amount", flat=True)
    )
    total_income = int(sum(income_total)) if income_total else 0

    # ✅ 変動費（今月）
    var_total = (
        MonthlyVariableExpense.objects
        .filter(month=month)
        .values_list("amount", flat=True)
    )
    total_variable = int(sum(var_total)) if var_total else 0

    # ✅ 固定費（テンプレの合計：今月も同じ想定）
    fixed_total = (
        FixedExpenseTemplate.objects
        .filter(is_active=True)
        .values_list("amount", flat=True)
    )
    total_fixed = int(sum(fixed_total)) if fixed_total else 0

    total_expense = total_variable + total_fixed

    # ✅ 銀行残高（今月）
    bal_total = (
        BankBalance.objects
        .filter(month=month)
        .values_list("balance", flat=True)
    )
    total_bank = int(sum(bal_total)) if bal_total else 0

    # ✅ 総資産（あなたのExcel思想：銀行残高は手入力、総資産は自動）
    total_assets = total_bank

    context = {
        "title": "家計簿",
        "month_label": f"{month.year}-{month.month:02d}",
        "total_income": total_income,
        "total_expense": total_expense,
        "total_bank": total_bank,
        "total_assets": total_assets,
        "total_fixed": total_fixed,
        "total_variable": total_variable,
    }
    return render(request, "kakeibo/dashboard.html", context)