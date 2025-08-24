from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
import json
from .models import BottomTab, SettingsPassword, SubMenu
from .forms import SettingsPasswordForm

# =============================
# 共通関数: 下タブ取得（サブメニュー付き）
# =============================
def get_bottom_tabs():
    tabs = BottomTab.objects.prefetch_related('submenus').order_by('order')
    tab_list = []
    for tab in tabs:
        tab_list.append({
            "id": tab.id,
            "name": tab.name,
            "icon": tab.icon,
            "url_name": tab.url_name,
            "link_type": tab.link_type,
            "order": tab.order,
            "submenus_list": [
                {
                    "id": sm.id,
                    "name": sm.name,
                    "url": sm.url,
                    "link_type": getattr(sm, 'link_type', 'view'),
                    "order": sm.order
                }
                for sm in tab.submenus.all().order_by('order')
            ]
        })
    return tab_list

# =============================
# メイン画面（ログイン必須）
# =============================
@login_required
def main_view(request):
    return render(request, "main.html", {
        "BOTTOM_TABS": get_bottom_tabs()
    })

# =============================
# ログイン／ログアウト
# =============================
def login_view(request):
    if request.user.is_authenticated:
        return redirect("main")

    if request.method == "POST":
        username = request.POST.get("username")
        password = request.POST.get("password")
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
def stock_list_view(request):
    return render(request, "stock_list.html", {"BOTTOM_TABS": get_bottom_tabs()})


def cash_view(request):
    return render(request, "cash.html", {"BOTTOM_TABS": get_bottom_tabs()})


def realized_view(request):
    return render(request, "realized.html", {"BOTTOM_TABS": get_bottom_tabs()})

# =============================
# 設定画面ログイン（DB保存パスワード使用）
# =============================
def settings_login(request):
    password_obj = SettingsPassword.objects.first()
    if not password_obj:
        return render(request, "settings_login.html", {
            "error": "パスワードが設定されていません。管理画面で作成してください。"
        })

    if request.method == "POST":
        password = request.POST.get("password")
        if password == password_obj.password:
            request.session["settings_authenticated"] = True
            return redirect("settings")
        else:
            messages.error(request, "パスワードが違います")

    return render(request, "settings_login.html")

# =============================
# 設定画面本体
# =============================
def settings_view(request):
    if not request.session.get("settings_authenticated"):
        return redirect("settings_login")

    return render(request, "settings.html", {
        "BOTTOM_TABS": get_bottom_tabs()
    })

# =============================
# --- 子ページ: 設定系 ---
# =============================
@login_required
def tab_manager_view(request):
    if not request.session.get("settings_authenticated"):
        return redirect("settings_login")
    return render(request, "tab_manager.html", {"BOTTOM_TABS": get_bottom_tabs()})

@login_required
def theme_settings_view(request):
    if not request.session.get("settings_authenticated"):
        return redirect("settings_login")
    return render(request, "theme_settings.html", {"BOTTOM_TABS": get_bottom_tabs()})

@login_required
def notification_settings_view(request):
    if not request.session.get("settings_authenticated"):
        return redirect("settings_login")
    return render(request, "notification_settings.html", {"BOTTOM_TABS": get_bottom_tabs()})

# =============================
# API: タブ一覧取得
# =============================
def get_tabs(request):
    tabs = get_bottom_tabs()
    return JsonResponse(tabs, safe=False)

# =============================
# API: タブ追加
# =============================
@csrf_exempt
def save_tab(request):
    if request.method == "POST":
        data = json.loads(request.body)
        tab = BottomTab.objects.create(
            name=data["name"],
            icon=data["icon"],
            url_name=data["url_name"],
            link_type=data.get("link_type", "view"),
            order=data.get("order", 0),
        )

        # サブメニューがあれば作成
        for idx, sm in enumerate(data.get("submenus", [])):
            tab.submenus.create(
                name=sm["name"],
                url=sm["url"],
                link_type=sm.get("link_type", "view"),
                order=idx
            )

        return JsonResponse({
            "id": tab.id,
            "name": tab.name,
            "icon": tab.icon,
            "url_name": tab.url_name,
            "link_type": tab.link_type,
            "order": tab.order,
            "submenus": [{"name": sm.name, "url": sm.url, "link_type": sm.link_type} for sm in tab.submenus.all()]
        })

    return JsonResponse({"error": "POST only"}, status=400)

# =============================
# API: タブ削除
# =============================
@csrf_exempt
def delete_tab(request, tab_id):
    try:
        tab = BottomTab.objects.get(id=tab_id)
        tab.delete()
        return JsonResponse({"success": True})
    except BottomTab.DoesNotExist:
        return JsonResponse({"error": "Not found"}, status=404)

# =============================
# 設定画面パスワード編集
# =============================
def settings_password_edit(request):
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

    return render(request, "settings_password_edit.html", {"form": form, "BOTTOM_TABS": get_bottom_tabs()})

# =============================
# API: 下タブの順番保存
# =============================
@csrf_exempt
def save_order(request):
    if request.method == "POST":
        data = json.loads(request.body)

        for tab_data in data:
            # タブ順更新
            try:
                tab = BottomTab.objects.get(id=tab_data['id'])
                tab.order = tab_data.get('order', 0)
                tab.save()
            except BottomTab.DoesNotExist:
                continue

            # サブメニュー順更新
            for sm_data in tab_data.get('submenus', []):
                sm_id = sm_data.get('id')
                if sm_id:
                    try:
                        sm = SubMenu.objects.get(id=sm_id)
                    except SubMenu.DoesNotExist:
                        continue
                else:
                    # 新規の場合は作成
                    sm = SubMenu()

                sm.name = sm_data.get('text', '').split(" → ")[0].strip()
                sm.url = sm_data.get('text', '').split(" → ")[1].split("[")[0].strip() if "→" in sm_data.get('text', '') else ''
                sm.link_type = sm_data.get('text', '').split("[")[1].replace("]","") if "[" in sm_data.get('text', '') else 'view'

                try:
                    sm.parent_tab = BottomTab.objects.get(id=sm_data.get('parent_id'))
                except BottomTab.DoesNotExist:
                    continue

                sm.order = sm_data.get('order', 0)
                sm.save()

        return JsonResponse({"status": "ok"})
    return JsonResponse({"status": "error", "message": "POST required"})

# =============================
# テンプレート用コンテキストプロセッサー
# =============================
def bottom_tabs_context(request):
    """全ページで共通の下タブを取得"""
    return {"BOTTOM_TABS": get_bottom_tabs()}
