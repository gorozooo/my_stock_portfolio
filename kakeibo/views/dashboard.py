# =========================================
# [FILE] dashboard.py
# [PATH] kakeibo/views/dashboard.py
#
# このファイルは何？
# 家計簿トップ画面（/kakeibo/）。
# - B案：月次方式
# - ?month=YYYY-MM で対象月を切り替え（前月/次月ボタン用）
# - 収入合計（MonthlyIncome）
# - 支出合計＝固定費テンプレ合計 + 変動費（月次）の合計
# - 差額（収入 - 支出）
# - 銀行残高合計（BankBalance：その月の合計）
# - owner別の内訳（家計 / B / G）
# =========================================

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Sum
from django.shortcuts import render
from django.utils import timezone

from .permissions import kakeibo_access_required
from ..models import MonthlyIncome, FixedExpenseTemplate, MonthlyVariableExpense, BankBalance


def month_first(d):
    return d.replace(day=1)


def add_month(d, delta: int):
    """
    何をする？
    - d (YYYY-MM-01) に delta ヶ月足した month を返す
    """
    y = d.year
    m = d.month + delta
    while m <= 0:
        y -= 1
        m += 12
    while m >= 13:
        y += 1
        m -= 12
    return d.replace(year=y, month=m, day=1)


def month_str(d):
    return d.strftime("%Y-%m")


def parse_month_param(request):
    """
    何をする？
    - ?month=YYYY-MM を受け取って Date(YYYY-MM-01) にする
    - なければ当月
    """
    today = timezone.localdate()
    base = month_first(today)

    s = (request.GET.get("month") or "").strip()
    if not s:
        return base

    try:
        y, m = s.split("-")
        y = int(y)
        m = int(m)
        return base.replace(year=y, month=m, day=1)
    except Exception:
        return base


def sum_by_owner(qs, owner_field="owner"):
    """
    何をする？
    - values(owner).annotate(sum) を dict にして返す
    - 返り値例：{"HOUSE": 10000, "B": 20000, "G": 0}
    """
    rows = qs.values(owner_field).annotate(s=Sum("amount"))
    out = {"HOUSE": 0, "B": 0, "G": 0}
    for r in rows:
        k = r.get(owner_field)
        v = r.get("s") or 0
        if k in out:
            out[k] = int(v)
    return out


@login_required
def dashboard(request):
    if not kakeibo_access_required(request.user):
        raise PermissionDenied("You do not have access to kakeibo.")

    m = parse_month_param(request)

    # ---- totals ----
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
    diff = (total_income or 0) - (total_expense or 0)

    # ---- bank total (month) ----
    bank_total = (
        BankBalance.objects.filter(month=m)
        .aggregate(s=Sum("balance"))["s"] or 0
    )

    # ---- breakdown by owner ----
    income_by = sum_by_owner(MonthlyIncome.objects.filter(month=m), owner_field="owner")

    fixed_by = sum_by_owner(
        FixedExpenseTemplate.objects.filter(is_active=True),
        owner_field="owner"
    )

    var_by = sum_by_owner(MonthlyVariableExpense.objects.filter(month=m), owner_field="owner")

    # bank is account__owner, and field is "balance" (not amount)
    bank_rows = BankBalance.objects.filter(month=m).values("account__owner").annotate(s=Sum("balance"))
    bank_by = {"HOUSE": 0, "B": 0, "G": 0}
    for r in bank_rows:
        k = r.get("account__owner")
        v = r.get("s") or 0
        if k in bank_by:
            bank_by[k] = int(v)

    # 支出owner別は 固定+変動 を合算
    expense_by = {
        "HOUSE": int(fixed_by["HOUSE"] + var_by["HOUSE"]),
        "B": int(fixed_by["B"] + var_by["B"]),
        "G": int(fixed_by["G"] + var_by["G"]),
    }

    context = {
        "title": "家計簿",
        "month": m,
        "month_label": month_str(m),
        "prev_month": add_month(m, -1),
        "next_month": add_month(m, 1),

        "total_income": int(total_income),
        "total_expense": int(total_expense),
        "fixed_sum": int(fixed_sum),
        "var_sum": int(var_sum),
        "diff": int(diff),
        "bank_total": int(bank_total),

        "income_by": income_by,
        "expense_by": expense_by,
        "fixed_by": fixed_by,
        "var_by": var_by,
        "bank_by": bank_by,
    }
    return render(request, "kakeibo/dashboard.html", context)