# =========================================
# [FILE] expense.py
# [PATH] kakeibo/views/expense.py
#
# このファイルは何？
# 支出入力画面（/kakeibo/expense/）を担当するView。
# 「カードまとめ」も「立替」も、この画面から登録する。
# =========================================

from datetime import date
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect, render

from .permissions import kakeibo_access_required
from ..forms import ExpenseForm


@login_required
def expense_create(request):
    if not kakeibo_access_required(request.user):
        raise PermissionDenied("You do not have access to kakeibo.")

    if request.method == "POST":
        form = ExpenseForm(request.POST)
        if form.is_valid():
            form.save()
            return redirect("kakeibo:dashboard")
    else:
        form = ExpenseForm(initial={"date": date.today()})

    return render(request, "kakeibo/transaction_form.html", {
        "title": "支出を追加",
        "form": form,
        "submit_label": "登録",
    })