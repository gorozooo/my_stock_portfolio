# =========================================
# [FILE] income.py
# [PATH] kakeibo/views/income.py
#
# このファイルは何？
# 収入入力画面（/kakeibo/income/）を担当するView。
# 月の入金など、家計側のプラスをシンプルに記録する。
# =========================================

from datetime import date
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect, render

from .permissions import kakeibo_access_required
from ..forms import IncomeForm


@login_required
def income_create(request):
    if not kakeibo_access_required(request.user):
        raise PermissionDenied("You do not have access to kakeibo.")

    if request.method == "POST":
        form = IncomeForm(request.POST)
        if form.is_valid():
            form.save()
            return redirect("kakeibo:dashboard")
    else:
        form = IncomeForm(initial={"date": date.today()})

    return render(request, "kakeibo/transaction_form.html", {
        "title": "収入を追加",
        "form": form,
        "submit_label": "登録",
    })