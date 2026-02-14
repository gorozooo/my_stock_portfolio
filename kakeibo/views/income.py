# =========================================
# [FILE] income.py
# [PATH] kakeibo/views/income.py
#
# このファイルは何？
# 収入入力（/kakeibo/income/）。
# - B案：月次方式（YYYY-MM）
# - owner（HOUSE/B/G） + 収入カテゴリ + 金額 + メモ
# - 口座/カードは不要（あなたの仕様どおり）
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
            return redirect("kakeibo:dashboard")
    else:
        form = MonthlyIncomeForm()

    return render(request, "kakeibo/form_simple.html", {
        "title": "収入（月次）を追加",
        "form": form,
        "submit_label": "登録",
        "help_text": "日付は「年月（YYYY-MM）」だけ。口座は選ばない方式。",
    })