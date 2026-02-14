# =========================================
# [FILE] income.py
# [PATH] kakeibo/views/income.py
#
# このファイルは何？
# 収入入力（/kakeibo/income/）。
# - B案：月次方式（YYYY-MM）
# - owner（HOUSE/B/G） + 収入カテゴリ + 金額 + メモ
# - 口座/カードは不要（あなたの仕様どおり）
#
# ★今回の改善
# - 追加だけでなく「登録済み一覧」「編集」「削除」も同じ画面でできる管理画面にする。
# - ?edit=<id> で編集モードに入り、POST action で create/update/delete を切り替える。
# =========================================

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect, render, get_object_or_404

from .permissions import kakeibo_access_required
from ..forms import MonthlyIncomeForm
from ..models import MonthlyIncome


@login_required
def income(request):
    if not kakeibo_access_required(request.user):
        raise PermissionDenied("You do not have access to kakeibo.")

    edit_id = request.GET.get("edit")
    edit_mode = False
    edit_obj = None

    if edit_id:
        edit_obj = get_object_or_404(MonthlyIncome, id=edit_id)
        edit_mode = True

    if request.method == "POST":
        action = request.POST.get("action") or ""

        if action == "create":
            form = MonthlyIncomeForm(request.POST)
            if form.is_valid():
                form.save()
                return redirect("/kakeibo/income/")

        elif action == "update":
            obj = get_object_or_404(MonthlyIncome, id=request.POST.get("id"))
            form = MonthlyIncomeForm(request.POST, instance=obj)
            if form.is_valid():
                form.save()
                return redirect("/kakeibo/income/")

        elif action == "delete":
            obj = get_object_or_404(MonthlyIncome, id=request.POST.get("id"))
            obj.delete()
            return redirect("/kakeibo/income/")

    # 一覧（最新200件）
    rows = MonthlyIncome.objects.order_by("-month", "-id")[:200]

    if edit_mode and edit_obj:
        form = MonthlyIncomeForm(instance=edit_obj)
    else:
        form = MonthlyIncomeForm()

    return render(request, "kakeibo/income_manage.html", {
        "title": "収入（月次）",
        "form": form,
        "rows": rows,
        "edit_mode": edit_mode,
        "edit_obj": edit_obj,
        "help_text": "日付は「年月（YYYY-MM）」だけ。口座は選ばない方式。",
    })