# =========================================
# [FILE] settings.py
# [PATH] kakeibo/views/settings.py
#
# このファイルは何？
# 家計簿の設定（/kakeibo/settings/）
# - タブ：収入 / 支出 / 口座 / カード
# - それぞれで追加・編集・削除
# - Category: name/code/order を管理（codeは必要な時だけ）
# - Account: owner/name を管理（kindはタブで固定）
# =========================================

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect, render, get_object_or_404

from .permissions import kakeibo_access_required
from ..forms import CategoryForm, AccountForm
from ..models import Category, Account


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
            edit_obj = get_object_or_404(Category, id=edit_id, type=("INCOME" if tab == "income" else "EXPENSE"))
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
                    obj.save()
                    return redirect(f"/kakeibo/settings/?tab={tab}")

            elif action == "update":
                obj = get_object_or_404(Category, id=request.POST.get("id"), type=ctype)
                form = CategoryForm(request.POST, instance=obj)
                if form.is_valid():
                    x = form.save(commit=False)
                    x.type = ctype
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