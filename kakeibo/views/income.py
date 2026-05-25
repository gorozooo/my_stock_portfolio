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
        "HOUSE": "家",
        "B": "ぼーや",
        "G": "ごろ",
    }
    return m.get(owner, owner or "不明")


def _income_session_key_month() -> str:
    return "kakeibo_income_last_month"


def _income_session_key_owner() -> str:
    return "kakeibo_income_last_owner"


def _save_income_last_values(request, month, owner: str) -> None:
    if month:
        try:
            request.session[_income_session_key_month()] = month.strftime("%Y-%m")
        except Exception:
            request.session[_income_session_key_month()] = str(month)
    if owner:
        request.session[_income_session_key_owner()] = owner


def _income_initial_from_session(request) -> dict:
    initial = {}
    month = (request.session.get(_income_session_key_month()) or "").strip()
    owner = (request.session.get(_income_session_key_owner()) or "").strip()
    if month:
        initial["month"] = month
    if owner:
        initial["owner"] = owner
    return initial


@login_required
def income(request):
    if not kakeibo_access_required(request.user):
        raise PermissionDenied("You do not have access to kakeibo.")

    if request.method == "POST":
        form = MonthlyIncomeForm(request.POST)
        if form.is_valid():
            obj = form.save()

            month = getattr(obj, "month", None)
            owner = getattr(obj, "owner", "")
            category = getattr(getattr(obj, "category", None), "name", "")
            amount = getattr(obj, "amount", None)

            _save_income_last_values(request, month=month, owner=owner)

            month_s = month.strftime("%Y-%m") if month else ""
            amount_s = f"{int(amount):,}" if amount is not None else ""

            msg = f"✅ 収入を登録：{month_s} / {_owner_label(owner)} / {category} / ¥{amount_s}"
            messages.success(request, msg)

            return redirect("kakeibo:income")
    else:
        form = MonthlyIncomeForm(initial=_income_initial_from_session(request))

    return render(request, "kakeibo/income_manage.html", {
        "title": "収入（月次）",
        "form": form,
    })