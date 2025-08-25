from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect
from django.http import JsonResponse, HttpResponseBadRequest
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.db import transaction
import json

from .models import BottomTab, SettingsPassword, SubMenu
from .forms import SettingsPasswordForm
from .utils import get_bottom_tabs  # utils.py から取り込み


# =============================
# 共通コンテキストプロセッサー
# =============================
def bottom_tabs_context(request):
    """全ページで共通の下タブを dict 形式で返す"""
    return {"BOTTOM_TABS": get_bottom_tabs()}


# =============================
# メイン画面
# =============================
@login_required
def main_view(request):
    return render(request, "main.html")


# =============================
# ログイン／ログアウト
# =============================
def login_view(request):
    if request.user.is_authenticated:
        return redirect("main")

    if request.method == "POST":
        username = request.POST.get("username") or ""
        password = request.POST.get("password") or ""
        user = authenticate(request, username=username, password=password)
        if user:
            login(request, user)
            return redirect("main")
        messages.error(request, "ユーザー名またはパスワードが違います。")

    return render(request, "auth_login.html")


def logout_view(request):
    logout(request)
    return redirect("login")


# =============================
# 株関連ページ
# =============================
@login_required
def stock_list_view(request):
    return render(request, "stock_list.html")


@login_required
def cash_view(request):
    return render(request, "cash.html")


@login_required
def realized_view(request):
    return render(request, "realized.html")

@login_required
def trade_history(request):
    # 今はダミーデータなので、何も渡さずにHTMLだけ表示
    return render(request, "trade_history.html")

# =============================
# 設定画面ログイン（DB保存パスワード）
# =============================
def settings_login(request):
    password_obj = SettingsPassword.objects.first()
    if not password_obj:
        return render(request, "settings_login.html", {
            "error": "パスワードが設定されていません。管理画面で作成してください。"
        })

    if request.method == "POST":
        password = request.POST.get("password") or ""
        if password == password_obj.password:
            request.session["settings_authenticated"] = True
            return redirect("settings")
        messages.error(request, "パスワードが違います")

    return render(request, "settings_login.html")


# =============================
# 設定画面本体
# =============================
@login_required
def settings_view(request):
    if not request.session.get("settings_authenticated"):
        return redirect("settings_login")
    return render(request, "settings.html")


# =============================
# 子ページ: 設定系
# =============================
@login_required
def tab_manager_view(request):
    if not request.session.get("settings_authenticated"):
        return redirect("settings_login")
    return render(request, "tab_manager.html")


@login_required
def theme_settings_view(request):
    if not request.session.get("settings_authenticated"):
        return redirect("settings_login")
    return render(request, "theme_settings.html")


@login_required
def notification_settings_view(request):
    if not request.session.get("settings_authenticated"):
        return redirect("settings_login")
    return render(request, "notification_settings.html")


# =============================
# API: タブ一覧取得
# =============================
def get_tabs(request):
    tabs = get_bottom_tabs()
    return JsonResponse(tabs, safe=False)


# =============================
# API: タブ追加／更新
# =============================
@csrf_exempt
@require_POST
@transaction.atomic
def save_tab(request):
    try:
        data = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return HttpResponseBadRequest("Invalid JSON")

    name = (data.get("name") or "").strip()
    icon = (data.get("icon") or "").strip()
    url_name = (data.get("url_name") or "").strip()
    link_type = (data.get("link_type") or "view").strip()
    submenus = data.get("submenus", [])
    tab_id = data.get("id")

    if not name or not icon or not url_name:
        return JsonResponse({"error": "必須項目が不足しています。"}, status=400)

    if tab_id:
        # 更新
        try:
            tab = BottomTab.objects.get(id=tab_id)
        except BottomTab.DoesNotExist:
            return JsonResponse({"error": "Tab not found"}, status=404)

        tab.name = name
        tab.icon = icon
        tab.url_name = url_name
        tab.link_type = link_type
        tab.save()

        # 既存サブメニューを削除して再生成
        tab.submenus.all().delete()
        for idx, sm in enumerate(submenus):
            tab.submenus.create(
                name=(sm.get("name") or "").strip(),
                url=(sm.get("url") or "").strip(),
                link_type=(sm.get("link_type") or "view").strip(),
                order=idx
            )
    else:
        # 新規
        max_tab = BottomTab.objects.order_by("-order").first()
        next_order = (max_tab.order + 1) if max_tab else 0
        tab = BottomTab.objects.create(
            name=name,
            icon=icon,
            url_name=url_name,
            link_type=link_type,
            order=next_order,
        )
        for idx, sm in enumerate(submenus):
            tab.submenus.create(
                name=(sm.get("name") or "").strip(),
                url=(sm.get("url") or "").strip(),
                link_type=(sm.get("link_type") or "view").strip(),
                order=idx
            )

    payload = {
        "id": tab.id,
        "name": tab.name,
        "icon": tab.icon,
        "url_name": tab.url_name,
        "link_type": tab.link_type,
        "order": tab.order,
        "submenus": [
            {"id": sm.id, "name": sm.name, "url": sm.url, "link_type": sm.link_type, "order": sm.order}
            for sm in tab.submenus.all().order_by("order")
        ],
    }
    return JsonResponse(payload)


# =============================
# API: タブ削除
# =============================
@csrf_exempt
@require_POST
@transaction.atomic
def delete_tab(request, tab_id):
    try:
        tab = BottomTab.objects.get(id=tab_id)
    except BottomTab.DoesNotExist:
        return JsonResponse({"error": "Not found"}, status=404)

    tab.delete()
    return JsonResponse({"success": True})


# =============================
# 設定画面パスワード編集
# =============================
@login_required
def settings_password_edit(request):
    if not request.session.get("settings_authenticated"):
        return redirect("settings_login")

    password_obj = SettingsPassword.objects.first()
    if not password_obj:
        password_obj = SettingsPassword.objects.create(password="")

    if request.method == "POST":
        form = SettingsPasswordForm(request.POST, instance=password_obj)
        if form.is_valid():
            form.save()
            messages.success(request, "パスワードを更新しました")
            return redirect("settings_password_edit")
    else:
        form = SettingsPasswordForm(instance=password_obj)

    return render(request, "settings_password_edit.html", {"form": form})


# =============================
# API: 下タブ＆サブメニューの順番保存
# =============================
@csrf_exempt
@require_POST
@transaction.atomic
def save_order(request):
    try:
        data = json.loads(request.body or "[]")
    except json.JSONDecodeError:
        return HttpResponseBadRequest("Invalid JSON")

    def parse_text(text: str):
        """
        '名前 → URL [type]' の文字列から name/url/type を抽出
        """
        text = text or ""
        name, url, ltype = text, "", "view"

        if "→" in text:
            parts = text.split("→", 1)
            name = parts[0].strip()
            right = parts[1].strip()
        else:
            right = ""

        if "[" in right and "]" in right:
            url_part, type_part = right.rsplit("[", 1)
            url = url_part.strip()
            ltype = type_part.replace("]", "").strip() or "view"
        elif right:
            url = right.strip()

        return name, url, ltype

    for tab_data in data:
        tab_id = tab_data.get("id")
        try:
            tab = BottomTab.objects.get(id=tab_id)
        except BottomTab.DoesNotExist:
            continue

        tab.order = int(tab_data.get("order", 0))
        tab.save()

        for sm_data in tab_data.get("submenus", []):
            sm_id = sm_data.get("id")
            parent_id = sm_data.get("parent_id") or tab.id
            order = int(sm_data.get("order", 0))
            name, url, ltype = parse_text(sm_data.get("text", ""))

            if sm_id:
                try:
                    sm = SubMenu.objects.get(id=sm_id)
                except SubMenu.DoesNotExist:
                    sm = SubMenu()
            else:
                sm = SubMenu()

            sm.name = name
            sm.url = url
            sm.link_type = ltype or "view"
            sm.parent_tab_id = parent_id
            sm.order = order
            sm.save()

    return JsonResponse({"status": "ok"})
