# =========================================
# [FILE] settings.py
# [PATH] kakeibo/views/settings.py
#
# このファイルは何？
# 家計簿の設定（/kakeibo/settings/）
# - タブ：収入 / 支出 / 口座 / カード
# - それぞれで追加・編集・削除
# - Category: name/order を管理（codeは自動で必要な時だけ入れる）
# - Account: owner/name を管理（kindはタブで固定）
#
# 重要：
# - 支出カテゴリで「カード」「立替」を作ったら、code を自動で入れる
#   - カード → code="CARD"
#   - 立替 → code="ADVANCE"
# - それ以外は code=""（空）
# =========================================

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect, render, get_object_or_404

from .permissions import kakeibo_access_required
from ..forms import CategoryForm, AccountForm
from ..models import Category, Account


def _auto_set_expense_category_code(obj: Category):
    """
    支出カテゴリの code を自動セットする。
    - 名前が「カード」 → CARD
    - 名前が「立替」   → ADVANCE
    - それ以外         → ""（空）
    """
    name = (obj.name or "").strip()

    if name == "カード":
        obj.code = "CARD"
    elif name == "立替":
        obj.code = "ADVANCE"
    else:
        obj.code = ""


@login_required
def settings_view(request):
    if not kakeibo_access_required(request.user):
        raise PermissionDenied("You do not have access to kakeibo.")

    tab = request.GET.get("tab") or request.POST.get("tab") or "expense"
    edit_id = request.GET.get("edit")

    def is_category_tab(t):
        return t in ("income", "expense")

    def is_account_tab(t):
        return t in ("account", "card")

    edit_mode = False
    edit_obj = None

    # GET: 編集対象
    if edit_id:
        if is_category_tab(tab):
            edit_obj = get_object_or_404(
                Category,
                id=edit_id,
                type=("INCOME" if tab == "income" else "EXPENSE"),
            )
            edit_mode = True
        elif is_account_tab(tab):
            kind = "ACCOUNT" if tab == "account" else "CARD"
            edit_obj = get_object_or_404(Account, id=edit_id, kind=kind)
            edit_mode = True

    # POST: create/update/delete
    if request.method == "POST":
        action = request.POST.get("action") or ""

        if is_category_tab(tab):
            ctype = "INCOME" if tab == "income" else "EXPENSE"

            if action == "create":
                form = CategoryForm(request.POST)
                if form.is_valid():
                    obj = form.save(commit=False)
                    obj.type = ctype

                    # ✅ 支出カテゴリだけ code を自動セット
                    if ctype == "EXPENSE":
                        _auto_set_expense_category_code(obj)
                    else:
                        # 収入カテゴリは code は使わない前提なので空で固定
                        obj.code = ""

                    obj.save()
                    return redirect(f"/kakeibo/settings/?tab={tab}")

            elif action == "update":
                obj = get_object_or_404(Category, id=request.POST.get("id"), type=ctype)
                form = CategoryForm(request.POST, instance=obj)
                if form.is_valid():
                    x = form.save(commit=False)
                    x.type = ctype

                    # ✅ 支出カテゴリだけ code を自動セット
                    if ctype == "EXPENSE":
                        _auto_set_expense_category_code(x)
                    else:
                        x.code = ""

                    x.save()
                    return redirect(f"/kakeibo/settings/?tab={tab}")

            elif action == "delete":
                obj = get_object_or_404(Category, id=request.POST.get("id"), type=ctype)
                obj.delete()
                return redirect(f"/kakeibo/settings/?tab={tab}")

        elif is_account_tab(tab):
            kind = "ACCOUNT" if tab == "account" else "CARD"

            if action == "create":
                form = AccountForm(request.POST)
                if form.is_valid():
                    obj = form.save(commit=False)
                    obj.kind = kind
                    obj.save()
                    return redirect(f"/kakeibo/settings/?tab={tab}")

            elif action == "update":
                obj = get_object_or_404(Account, id=request.POST.get("id"), kind=kind)
                form = AccountForm(request.POST, instance=obj)
                if form.is_valid():
                    x = form.save(commit=False)
                    x.kind = kind
                    x.save()
                    return redirect(f"/kakeibo/settings/?tab={tab}")

            elif action == "delete":
                obj = get_object_or_404(Account, id=request.POST.get("id"), kind=kind)
                obj.delete()
                return redirect(f"/kakeibo/settings/?tab={tab}")

    # 一覧＆フォーム
    if is_category_tab(tab):
        ctype = "INCOME" if tab == "income" else "EXPENSE"
        rows = Category.objects.filter(type=ctype).order_by("order", "id")
        form = CategoryForm(instance=edit_obj) if (edit_mode and edit_obj) else CategoryForm()
    else:
        kind = "ACCOUNT" if tab == "account" else "CARD"
        rows = Account.objects.filter(kind=kind).order_by("owner", "id")
        form = AccountForm(instance=edit_obj) if (edit_mode and edit_obj) else AccountForm()

    return render(request, "kakeibo/settings.html", {
        "title": "家計簿 設定",
        "tab": tab,
        "rows": rows,
        "form": form,
        "edit_mode": edit_mode,
        "edit_obj": edit_obj,
    })