# =========================================
# [FILE] dashboard.py
# [PATH] kakeibo/views/dashboard.py
#
# このファイルは何？
# 家計簿トップ画面（/kakeibo/）を表示するView。
# 夫婦グループ以外はアクセスできないよう制御する。
# =========================================

from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from .permissions import kakeibo_access_required


@login_required
def dashboard(request):
    if not kakeibo_access_required(request.user):
        raise PermissionDenied("You do not have access to kakeibo.")

    context = {
        "title": "家計簿ダッシュボード",
    }
    return render(request, "kakeibo/dashboard.html", context)