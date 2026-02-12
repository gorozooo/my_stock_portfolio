# =========================================
# [FILE] settings.py
# [PATH] kakeibo/views/settings.py
#
# このファイルは何？
# 家計簿の設定画面（/kakeibo/settings/）。
# まずは口座/カード（Account）を追加できるようにする。
# =========================================

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect, render

from .permissions import kakeibo_access_required
from ..forms import AccountForm
from ..models import Account


@login_required
def settings_view(request):
    if not kakeibo_access_required(request.user):
        raise PermissionDenied("You do not have access to kakeibo.")

    if request.method == "POST":
        form = AccountForm(request.POST)
        if form.is_valid():
            form.save()
            return redirect("kakeibo:settings")
    else:
        form = AccountForm()

    accounts = Account.objects.order_by("id")

    return render(request, "kakeibo/settings.html", {
        "title": "家計簿 設定",
        "form": form,
        "accounts": accounts,
    })