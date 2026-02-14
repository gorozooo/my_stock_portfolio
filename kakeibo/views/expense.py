# =========================================
# [FILE] expense.py
# [PATH] kakeibo/views/expense.py
#
# このファイルは何？
# 支出画面（/kakeibo/expense/）。
# - Transaction方式は廃止
# - いまは「固定費/変動費 分岐UI」を作る前の仮画面（django checkを通すため）
# =========================================

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import render

from .permissions import kakeibo_access_required


@login_required
def expense_create(request):
    if not kakeibo_access_required(request.user):
        raise PermissionDenied("You do not have access to kakeibo.")

    # ✅ いったん落ちない仮ページ
    return render(request, "kakeibo/coming_soon.html", {
        "title": "支出",
        "message": "支出（固定費/変動費の分岐）は次のステップで実装します。",
    })