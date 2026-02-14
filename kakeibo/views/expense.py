# =========================================
# [FILE] expense.py
# [PATH] kakeibo/views/expense.py
#
# このファイルは何？
# 支出（/kakeibo/expense/）
# - 入口で「固定費 / 変動費」を選ぶ
# - 固定費：テンプレ（毎月同じ）
# - 変動費：月次（カード/立替）
# =========================================

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect, render, get_object_or_404

from .permissions import kakeibo_access_required
from ..forms import FixedExpenseTemplateForm, MonthlyVariableExpenseForm
from ..models import FixedExpenseTemplate, MonthlyVariableExpense


@login_required
def expense(request):
    if not kakeibo_access_required(request.user):
        raise PermissionDenied("You do not have access to kakeibo.")

    return render(request, "kakeibo/expense_menu.html", {
        "title": "支出",
    })


@login_required
def expense_fixed(request):
    if not kakeibo_access_required(request.user):
        raise PermissionDenied("You do not have access to kakeibo.")

    edit_id = request.GET.get("edit")
    edit_mode = False
    edit_obj = None

    if edit_id:
        edit_obj = get_object_or_404(FixedExpenseTemplate, id=edit_id)
        edit_mode = True

    if request.method == "POST":
        action = request.POST.get("action") or ""

        if action == "create":
            form = FixedExpenseTemplateForm(request.POST)
            if form.is_valid():
                form.save()
                return redirect("/kakeibo/expense/fixed/")

        elif action == "update":
            obj = get_object_or_404(FixedExpenseTemplate, id=request.POST.get("id"))
            form = FixedExpenseTemplateForm(request.POST, instance=obj)
            if form.is_valid():
                form.save()
                return redirect("/kakeibo/expense/fixed/")

        elif action == "delete":
            obj = get_object_or_404(FixedExpenseTemplate, id=request.POST.get("id"))
            obj.delete()
            return redirect("/kakeibo/expense/fixed/")

    rows = FixedExpenseTemplate.objects.order_by("-is_active", "id")

    if edit_mode and edit_obj:
        form = FixedExpenseTemplateForm(instance=edit_obj)
    else:
        form = FixedExpenseTemplateForm()

    return render(request, "kakeibo/manage_list.html", {
        "title": "固定費（毎月）",
        "form": form,
        "rows": rows,
        "edit_mode": edit_mode,
        "edit_obj": edit_obj,
        "tab_key": "fixed",
    })


@login_required
def expense_variable(request):
    if not kakeibo_access_required(request.user):
        raise PermissionDenied("You do not have access to kakeibo.")

    edit_id = request.GET.get("edit")
    edit_mode = False
    edit_obj = None

    if edit_id:
        edit_obj = get_object_or_404(MonthlyVariableExpense, id=edit_id)
        edit_mode = True

    if request.method == "POST":
        action = request.POST.get("action") or ""

        if action == "create":
            form = MonthlyVariableExpenseForm(request.POST)
            if form.is_valid():
                form.save()
                return redirect("/kakeibo/expense/variable/")

        elif action == "update":
            obj = get_object_or_404(MonthlyVariableExpense, id=request.POST.get("id"))
            form = MonthlyVariableExpenseForm(request.POST, instance=obj)
            if form.is_valid():
                form.save()
                return redirect("/kakeibo/expense/variable/")

        elif action == "delete":
            obj = get_object_or_404(MonthlyVariableExpense, id=request.POST.get("id"))
            obj.delete()
            return redirect("/kakeibo/expense/variable/")

    rows = MonthlyVariableExpense.objects.order_by("-month", "-id")[:200]

    if edit_mode and edit_obj:
        form = MonthlyVariableExpenseForm(instance=edit_obj)
    else:
        form = MonthlyVariableExpenseForm()

    return render(request, "kakeibo/manage_list.html", {
        "title": "変動費（月次：カード/立替）",
        "form": form,
        "rows": rows,
        "edit_mode": edit_mode,
        "edit_obj": edit_obj,
        "tab_key": "variable",
    })