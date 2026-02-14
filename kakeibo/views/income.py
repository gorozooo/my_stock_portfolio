# =========================================
# [FILE] income.py
# [PATH] kakeibo/views/income.py
#
# このファイルは何？
# 収入画面（/kakeibo/income/）。
# - Transaction方式は廃止
# - いまは「月次方式の収入入力」を作る前の仮画面（django checkを通すため）
# =========================================

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import render

from .permissions import kakeibo_access_required


@login_required
def income_create(request):
    if not kakeibo_access_required(request.user):
        raise PermissionDenied("You do not have access to kakeibo.")

    # ✅ いったん落ちない仮ページ
    return render(request, "kakeibo/coming_soon.html", {
        "title": "収入",
        "message": "収入（月次方式）は次のステップで実装します。",
    })