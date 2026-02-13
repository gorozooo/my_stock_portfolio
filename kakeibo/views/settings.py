# =========================================
# [FILE] settings.py
# [PATH] kakeibo/views/settings.py
#
# このファイルは何？
# 家計簿の設定画面（/kakeibo/settings/）。
# - 口座/カード（Account）の追加
# - 登録済み口座/カードの削除（誤操作防止でPOST + confirm）
# =========================================

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect, render, get_object_or_404
from django.views.decorators.http import require_POST
from django.contrib import messages

from .permissions import kakeibo_access_required
from ..forms import AccountForm
from ..models import Account, Transaction


@login_required
def settings_view(request):
    if not kakeibo_access_required(request.user):
        raise PermissionDenied("You do not have access to kakeibo.")

    if request.method == "POST":
        form = AccountForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "口座/カードを追加しました。")
            return redirect("kakeibo:settings")
    else:
        form = AccountForm()

    accounts = Account.objects.order_by("id")

    # 画面で“分類”の意味が分かるように説明を固定表示
    category_hints = [
        ("カードまとめ", "カード請求の月合計（明細は入れない運用）"),
        ("固定費", "家賃/サブスク/通信費など毎月の固定"),
        ("立替", "誰かの分を立て替えた支出（メモで対象を書く）"),
        ("その他", "上に当てはまらないもの"),
    ]

    return render(request, "kakeibo/settings.html", {
        "title": "家計簿 設定",
        "form": form,
        "accounts": accounts,
        "category_hints": category_hints,
    })


@login_required
@require_POST
def account_delete(request, pk: int):
    if not kakeibo_access_required(request.user):
        raise PermissionDenied("You do not have access to kakeibo.")

    a = get_object_or_404(Account, pk=pk)

    # 口座に取引が紐づいてたら削除禁止（事故防止）
    used = Transaction.objects.filter(account=a).exists()
    if used:
        messages.error(request, "この口座/カードは取引に使われているため削除できません。")
        return redirect("kakeibo:settings")

    a.delete()
    messages.success(request, "口座/カードを削除しました。")
    return redirect("kakeibo:settings")