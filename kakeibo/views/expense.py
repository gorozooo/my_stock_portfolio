# =========================================
# [FILE] expense.py
# [PATH] kakeibo/views/expense.py
#
# このファイルは何？
# 支出（/kakeibo/expense/）
# - 入口で「固定費 / 変動費」を選ぶ
# - 固定費：テンプレ（毎月同じ）
# - 変動費：月次（カード/立替）
#
# ★重要（今回の修正）
# POSTでフォームが invalid のとき、
# エラー付きフォームを捨てずにそのまま画面へ返す（＝登録されない原因の見える化）
#
# ★今回：登録/更新/削除が成功したらトースト（messages）を出す
# =========================================

from django.contrib import messages
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

    # ★formはここで作って、POSTでinvalidでも捨てない
    form = FixedExpenseTemplateForm(instance=edit_obj) if (edit_mode and edit_obj) else FixedExpenseTemplateForm()

    if request.method == "POST":
        action = request.POST.get("action") or ""

        if action == "create":
            form = FixedExpenseTemplateForm(request.POST)
            if form.is_valid():
                form.save()
                messages.success(request, "固定費を登録しました。")
                return redirect("/kakeibo/expense/fixed/")

        elif action == "update":
            obj = get_object_or_404(FixedExpenseTemplate, id=request.POST.get("id"))
            edit_obj = obj
            edit_mode = True
            form = FixedExpenseTemplateForm(request.POST, instance=obj)
            if form.is_valid():
                form.save()
                messages.success(request, "固定費を更新しました。")
                return redirect("/kakeibo/expense/fixed/")

        elif action == "delete":
            obj = get_object_or_404(FixedExpenseTemplate, id=request.POST.get("id"))
            obj.delete()
            messages.success(request, "固定費を削除しました。")
            return redirect("/kakeibo/expense/fixed/")

    rows = FixedExpenseTemplate.objects.order_by("-is_active", "id")

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

    # ★formはここで作って、POSTでinvalidでも捨てない
    form = MonthlyVariableExpenseForm(instance=edit_obj) if (edit_mode and edit_obj) else MonthlyVariableExpenseForm()

    if request.method == "POST":
        action = request.POST.get("action") or ""

        if action == "create":
            form = MonthlyVariableExpenseForm(request.POST)
            if form.is_valid():
                form.save()
                messages.success(request, "変動費を登録しました。")
                return redirect("/kakeibo/expense/variable/")

        elif action == "update":
            obj = get_object_or_404(MonthlyVariableExpense, id=request.POST.get("id"))
            edit_obj = obj
            edit_mode = True
            form = MonthlyVariableExpenseForm(request.POST, instance=obj)
            if form.is_valid():
                form.save()
                messages.success(request, "変動費を更新しました。")
                return redirect("/kakeibo/expense/variable/")

        elif action == "delete":
            obj = get_object_or_404(MonthlyVariableExpense, id=request.POST.get("id"))
            obj.delete()
            messages.success(request, "変動費を削除しました。")
            return redirect("/kakeibo/expense/variable/")

    rows = MonthlyVariableExpense.objects.order_by("-month", "-id")[:200]

    return render(request, "kakeibo/manage_list.html", {
        "title": "変動費（月次：カード/立替）",
        "form": form,
        "rows": rows,
        "edit_mode": edit_mode,
        "edit_obj": edit_obj,
        "tab_key": "variable",
    })