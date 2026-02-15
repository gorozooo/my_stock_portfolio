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
#
# ★今回：登録が成功したら「何を登録したか」までトースト表示する
# =========================================

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect, render

from .permissions import kakeibo_access_required
from ..forms import MonthlyIncomeForm


def _owner_label(owner: str) -> str:
    m = {
        "HOUSE": "家計",
        "B": "B（夫）",
        "G": "G（妻）",
    }
    return m.get(owner, owner or "不明")


@login_required
def income(request):
    if not kakeibo_access_required(request.user):
        raise PermissionDenied("You do not have access to kakeibo.")

    if request.method == "POST":
        form = MonthlyIncomeForm(request.POST)
        if form.is_valid():
            obj = form.save()

            # トースト文言（内容まで表示）
            month = getattr(obj, "month", None)
            owner = getattr(obj, "owner", "")
            category = getattr(getattr(obj, "category", None), "name", "")
            amount = getattr(obj, "amount", None)

            month_s = month.strftime("%Y-%m") if month else ""
            amount_s = f"{int(amount):,}" if amount is not None else ""

            msg = f"✅ 収入を登録：{month_s} / {_owner_label(owner)} / {category} / ¥{amount_s}"
            messages.success(request, msg)

            return redirect("kakeibo:income")
    else:
        form = MonthlyIncomeForm()

    return render(request, "kakeibo/income_manage.html", {
        "title": "収入（月次）",
        "form": form,
    })