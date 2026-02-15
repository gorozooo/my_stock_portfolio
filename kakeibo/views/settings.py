# =========================================
# [FILE] settings.py
# [PATH] kakeibo/views/settings.py
#
# このファイルは何？
# 家計簿の設定
# - /kakeibo/settings/      : 設定メニュー（入口）
# - /kakeibo/settings/edit/ : 設定の編集（収入/支出/口座/カード）
# =========================================

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect, render, get_object_or_404

from .permissions import kakeibo_access_required
from ..forms import CategoryForm, AccountForm
from ..models import Category, Account


@login_required
def settings_menu(request):
    """
    このViewは何？
    - 下タブ「設定」から最初に出す「選択画面」。
    - ここから「収入/支出/口座/カード/管理」へ移動する。
    """
    if not kakeibo_access_required(request.user):
        raise PermissionDenied("You do not have access to kakeibo.")

    return render(request, "kakeibo/settings_menu.html", {
        "title": "設定",
    })


@login_required
def settings_view(request):
    """
    このViewは何？
    - 「収入/支出/口座/カード」を編集する画面。
    - 入口は settings_menu から（/kakeibo/settings/edit/?tab=...）
    - 上タブは使わない（メニュー方式）
    """
    if not kakeibo_access_required(request.user):
        raise PermissionDenied("You do not have access to kakeibo.")

    tab = request.GET.get("tab") or request.POST.get("tab") or "expense"
    edit_id = request.GET.get("edit")

    def is_category_tab(t):
        return t in ("income", "expense")

    def is_account_tab(t):
        return t in ("account", "card")

    # 表示タイトル（iPhoneで迷わない用）
    title_map = {
        "income": "収入",
        "expense": "支出",
        "account": "口座",
        "card": "カード",
    }
    page_title = title_map.get(tab, "設定")

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
                    obj.save()
                    return redirect(f"/kakeibo/settings/edit/?tab={tab}")

            elif action == "update":
                obj = get_object_or_404(Category, id=request.POST.get("id"), type=ctype)
                form = CategoryForm(request.POST, instance=obj)
                if form.is_valid():
                    x = form.save(commit=False)
                    x.type = ctype
                    x.save()
                    return redirect(f"/kakeibo/settings/edit/?tab={tab}")

            elif action == "delete":
                obj = get_object_or_404(Category, id=request.POST.get("id"), type=ctype)
                obj.delete()
                return redirect(f"/kakeibo/settings/edit/?tab={tab}")

        elif is_account_tab(tab):
            kind = "ACCOUNT" if tab == "account" else "CARD"

            if action == "create":
                form = AccountForm(request.POST)
                if form.is_valid():
                    obj = form.save(commit=False)
                    obj.kind = kind
                    obj.save()
                    return redirect(f"/kakeibo/settings/edit/?tab={tab}")

            elif action == "update":
                obj = get_object_or_404(Account, id=request.POST.get("id"), kind=kind)
                form = AccountForm(request.POST, instance=obj)
                if form.is_valid():
                    x = form.save(commit=False)
                    x.kind = kind
                    x.save()
                    return redirect(f"/kakeibo/settings/edit/?tab={tab}")

            elif action == "delete":
                obj = get_object_or_404(Account, id=request.POST.get("id"), kind=kind)
                obj.delete()
                return redirect(f"/kakeibo/settings/edit/?tab={tab}")

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
        "title": page_title,
        "tab": tab,
        "rows": rows,
        "form": form,
        "edit_mode": edit_mode,
        "edit_obj": edit_obj,
    })