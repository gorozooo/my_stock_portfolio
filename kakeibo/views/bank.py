# =========================================
# [FILE] bank.py
# [PATH] kakeibo/views/bank.py
#
# このファイルは何？
# 銀行（/kakeibo/bank/）
# - 月次で「口座残高」を手入力する（入力のみ）
# - 口座は設定タブの「口座」で追加（owner=B/G/HOUSE）
# - ★B案：入力画面に登録済み一覧を出さない（管理で探す）
#
# ★今回：登録/更新が成功したらトースト（messages）を出す
# =========================================

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect, render, get_object_or_404
from django.utils import timezone

from .permissions import kakeibo_access_required
from ..forms import BankBalanceForm
from ..models import BankBalance


@login_required
def bank(request):
    if not kakeibo_access_required(request.user):
        raise PermissionDenied("You do not have access to kakeibo.")

    edit_id = request.GET.get("edit")
    edit_mode = False
    edit_obj = None

    if edit_id:
        edit_obj = get_object_or_404(BankBalance, id=edit_id)
        edit_mode = True

    if request.method == "POST":
        action = request.POST.get("action") or ""

        if action == "create":
            form = BankBalanceForm(request.POST)
            if form.is_valid():
                # unique_together(month, account) なので、同月同口座は上書き（get_or_create）
                m = form.cleaned_data["month"]
                acc = form.cleaned_data["account"]
                bal = form.cleaned_data["balance"]

                obj, created = BankBalance.objects.get_or_create(month=m, account=acc, defaults={"balance": bal})
                if not created and obj.balance != bal:
                    obj.balance = bal
                    obj.save()
                    messages.success(request, "銀行残高を更新しました。")
                elif created:
                    messages.success(request, "銀行残高を登録しました。")
                else:
                    messages.success(request, "銀行残高は変更なしでした。")

                return redirect("/kakeibo/bank/")
            # invalid はそのまま返す

        elif action == "update":
            obj = get_object_or_404(BankBalance, id=request.POST.get("id"))
            form = BankBalanceForm(request.POST, instance=obj)
            if form.is_valid():
                form.save()
                messages.success(request, "銀行残高を更新しました。")
                return redirect("/kakeibo/bank/")
            # invalid はそのまま返す

        elif action == "delete":
            # 入力画面では削除しない方針（管理でやる）
            return redirect("/kakeibo/bank/")

    if edit_mode and edit_obj:
        form = BankBalanceForm(instance=edit_obj)
    else:
        form = BankBalanceForm(initial={"month": timezone.localdate().replace(day=1)})

    return render(request, "kakeibo/bank.html", {
        "title": "銀行残高（月次）",
        "form": form,
        "edit_mode": edit_mode,
        "edit_obj": edit_obj,
    })