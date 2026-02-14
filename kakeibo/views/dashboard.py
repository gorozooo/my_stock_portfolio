# =========================================
# [FILE] dashboard.py
# [PATH] kakeibo/views/dashboard.py
#
# このファイルは何？
# 家計簿トップ画面（/kakeibo/）。
# - B案：月次方式
# - 今月の収入合計（MonthlyIncome）
# - 今月の支出合計＝固定費テンプレ合計 + 変動費（月次）の合計
# =========================================

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Sum
from django.shortcuts import render
from django.utils import timezone

from .permissions import kakeibo_access_required
from ..models import MonthlyIncome, FixedExpenseTemplate, MonthlyVariableExpense


def month_first(d):
    return d.replace(day=1)


@login_required
def dashboard(request):
    if not kakeibo_access_required(request.user):
        raise PermissionDenied("You do not have access to kakeibo.")

    today = timezone.localdate()
    m = month_first(today)

    total_income = MonthlyIncome.objects.filter(month=m).aggregate(s=Sum("amount"))["s"] or 0

    fixed_sum = (
        FixedExpenseTemplate.objects.filter(is_active=True)
        .aggregate(s=Sum("amount"))["s"] or 0
    )

    var_sum = (
        MonthlyVariableExpense.objects.filter(month=m)
        .aggregate(s=Sum("amount"))["s"] or 0
    )

    total_expense = fixed_sum + var_sum

    context = {
        "title": "家計簿",
        "month_label": f"{m.year}-{m.month:02d}",
        "total_income": int(total_income),
        "total_expense": int(total_expense),
        "fixed_sum": int(fixed_sum),
        "var_sum": int(var_sum),
    }
    return render(request, "kakeibo/dashboard.html", context)