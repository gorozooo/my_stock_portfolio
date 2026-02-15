# =========================================
# [FILE] income.py
# [PATH] kakeibo/views/income.py
#
# このファイルは何？
# 収入入力（/kakeibo/income/）。
# - B案：月次方式（YYYY-MM）
# - owner（HOUSE/B/G） + 収入カテゴリ + 金額 + メモ
# - ★B案：入力画面から「登録済み一覧」を完全に消す（スッキリ特化）
# - 編集/削除は「設定 → 管理（探して編集・削除） → 収入（管理）」で行う
# =========================================

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect, render

from .permissions import kakeibo_access_required
from ..forms import MonthlyIncomeForm


@login_required
def income(request):
    if not kakeibo_access_required(request.user):
        raise PermissionDenied("You do not have access to kakeibo.")

    if request.method == "POST":
        form = MonthlyIncomeForm(request.POST)
        if form.is_valid():
            form.save()
            return redirect("kakeibo:income")
    else:
        form = MonthlyIncomeForm()

    return render(request, "kakeibo/income_manage.html", {
        "title": "収入（月次）",
        "form": form,
    })