# =========================================
# [FILE] manage.py
# [PATH] kakeibo/views/manage.py
#
# このファイルは何？
# 「管理（探して編集/削除）」専用の画面群。
# - 入力画面とは分離して、データが増えても探しやすいようにする。
# - 月次データは YYYY-MM で絞り込みできる（デフォルトは当月）。
# =========================================

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect, render, get_object_or_404
from django.utils import timezone

from .permissions import kakeibo_access_required
from ..forms import (
    MonthlyIncomeForm,
    MonthlyVariableExpenseForm,
    FixedExpenseTemplateForm,
    BankBalanceForm,
)
from ..models import (
    MonthlyIncome,
    MonthlyVariableExpense,
    FixedExpenseTemplate,
    BankBalance,
)


def _month_from_get(request):
    """
    何をする？
    - ?month=YYYY-MM を受け取って Date(YYYY-MM-01) にする
    - なければ当月
    """
    s = request.GET.get("month")
    today = timezone.localdate().replace(day=1)
    if not s:
        return today

    # "YYYY-MM" を安全に Date にする
    try:
        y, m = s.split("-")
        y = int(y)
        m = int(m)
        return today.replace(year=y, month=m, day=1)
    except Exception:
        return today


@login_required
def manage_menu(request):
    if not kakeibo_access_required(request.user):
        raise PermissionDenied("You do not have access to kakeibo.")

    return render(request, "kakeibo/manage_menu.html", {
        "title": "管理（探して編集・削除）",
    })


@login_required
def manage_income(request):
    if not kakeibo_access_required(request.user):
        raise PermissionDenied("You do not have access to kakeibo.")

    month = _month_from_get(request)
    owner = request.GET.get("owner") or ""

    qs = MonthlyIncome.objects.filter(month=month).order_by("-id")
    if owner:
        qs = qs.filter(owner=owner)

    edit_id = request.GET.get("edit")
    edit_mode = False
    edit_obj = None

    if edit_id:
        edit_obj = get_object_or_404(MonthlyIncome, id=edit_id)
        edit_mode = True

    if request.method == "POST":
        action = request.POST.get("action") or ""
        if action == "update":
            obj = get_object_or_404(MonthlyIncome, id=request.POST.get("id"))
            form = MonthlyIncomeForm(request.POST, instance=obj)
            if form.is_valid():
                form.save()
                return redirect(f"/kakeibo/manage/income/?month={month:%Y-%m}&owner={owner}")
        elif action == "delete":
            obj = get_object_or_404(MonthlyIncome, id=request.POST.get("id"))
            obj.delete()
            return redirect(f"/kakeibo/manage/income/?month={month:%Y-%m}&owner={owner}")

    form = MonthlyIncomeForm(instance=edit_obj) if (edit_mode and edit_obj) else None

    return render(request, "kakeibo/manage_month_list.html", {
        "title": "収入（管理）",
        "kind": "income",
        "month": month,
        "owner": owner,
        "rows": qs,
        "edit_mode": edit_mode,
        "edit_obj": edit_obj,
        "form": form,
    })


@login_required
def manage_variable(request):
    if not kakeibo_access_required(request.user):
        raise PermissionDenied("You do not have access to kakeibo.")

    month = _month_from_get(request)
    owner = request.GET.get("owner") or ""

    qs = MonthlyVariableExpense.objects.filter(month=month).order_by("-id")
    if owner:
        qs = qs.filter(owner=owner)

    edit_id = request.GET.get("edit")
    edit_mode = False
    edit_obj = None

    if edit_id:
        edit_obj = get_object_or_404(MonthlyVariableExpense, id=edit_id)
        edit_mode = True

    if request.method == "POST":
        action = request.POST.get("action") or ""
        if action == "update":
            obj = get_object_or_404(MonthlyVariableExpense, id=request.POST.get("id"))
            form = MonthlyVariableExpenseForm(request.POST, instance=obj)
            if form.is_valid():
                form.save()
                return redirect(f"/kakeibo/manage/variable/?month={month:%Y-%m}&owner={owner}")
        elif action == "delete":
            obj = get_object_or_404(MonthlyVariableExpense, id=request.POST.get("id"))
            obj.delete()
            return redirect(f"/kakeibo/manage/variable/?month={month:%Y-%m}&owner={owner}")

    form = MonthlyVariableExpenseForm(instance=edit_obj) if (edit_mode and edit_obj) else None

    return render(request, "kakeibo/manage_month_list.html", {
        "title": "変動費（管理）",
        "kind": "variable",
        "month": month,
        "owner": owner,
        "rows": qs,
        "edit_mode": edit_mode,
        "edit_obj": edit_obj,
        "form": form,
    })


@login_required
def manage_bank(request):
    if not kakeibo_access_required(request.user):
        raise PermissionDenied("You do not have access to kakeibo.")

    month = _month_from_get(request)
    owner = request.GET.get("owner") or ""

    qs = BankBalance.objects.filter(month=month).select_related("account").order_by("-id")
    if owner:
        qs = qs.filter(account__owner=owner)

    edit_id = request.GET.get("edit")
    edit_mode = False
    edit_obj = None

    if edit_id:
        edit_obj = get_object_or_404(BankBalance, id=edit_id)
        edit_mode = True

    if request.method == "POST":
        action = request.POST.get("action") or ""
        if action == "update":
            obj = get_object_or_404(BankBalance, id=request.POST.get("id"))
            form = BankBalanceForm(request.POST, instance=obj)
            if form.is_valid():
                form.save()
                return redirect(f"/kakeibo/manage/bank/?month={month:%Y-%m}&owner={owner}")
        elif action == "delete":
            obj = get_object_or_404(BankBalance, id=request.POST.get("id"))
            obj.delete()
            return redirect(f"/kakeibo/manage/bank/?month={month:%Y-%m}&owner={owner}")

    form = BankBalanceForm(instance=edit_obj) if (edit_mode and edit_obj) else None

    return render(request, "kakeibo/manage_month_list.html", {
        "title": "銀行残高（管理）",
        "kind": "bank",
        "month": month,
        "owner": owner,
        "rows": qs,
        "edit_mode": edit_mode,
        "edit_obj": edit_obj,
        "form": form,
    })


@login_required
def manage_fixed(request):
    if not kakeibo_access_required(request.user):
        raise PermissionDenied("You do not have access to kakeibo.")

    owner = request.GET.get("owner") or ""

    qs = FixedExpenseTemplate.objects.order_by("-is_active", "id")
    if owner:
        qs = qs.filter(owner=owner)

    edit_id = request.GET.get("edit")
    edit_mode = False
    edit_obj = None

    if edit_id:
        edit_obj = get_object_or_404(FixedExpenseTemplate, id=edit_id)
        edit_mode = True

    if request.method == "POST":
        action = request.POST.get("action") or ""
        if action == "update":
            obj = get_object_or_404(FixedExpenseTemplate, id=request.POST.get("id"))
            form = FixedExpenseTemplateForm(request.POST, instance=obj)
            if form.is_valid():
                form.save()
                return redirect(f"/kakeibo/manage/fixed/?owner={owner}")
        elif action == "delete":
            obj = get_object_or_404(FixedExpenseTemplate, id=request.POST.get("id"))
            obj.delete()
            return redirect(f"/kakeibo/manage/fixed/?owner={owner}")

    form = FixedExpenseTemplateForm(instance=edit_obj) if (edit_mode and edit_obj) else None

    return render(request, "kakeibo/manage_fixed_list.html", {
        "title": "固定費（管理）",
        "owner": owner,
        "rows": qs,
        "edit_mode": edit_mode,
        "edit_obj": edit_obj,
        "form": form,
    })