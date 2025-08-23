from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
import json
from .models import BottomTab
from .models import SettingsPassword
from django.shortcuts import redirect, render
from .forms import SettingsPasswordForm

@login_required
def main_view(request):
    return render(request, "main.html")

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

def stock_list_view(request):
    return render(request, 'stock_list.html')

def cash_view(request):
    return render(request, 'cash.html')

def realized_view(request):
    return render(request, 'realized.html')

from django.contrib import messages

SETTINGS_PASSWORD = "123"  # ← 好きなパスワードに設定

def settings_login(request):
    """設定画面ログイン（DBパスワード参照）"""
    password_obj = SettingsPassword.objects.first()
    if not password_obj:
        return render(request, "portfolio/settings_login.html", {
            "error": "パスワードが設定されていません。管理画面で作成してください。"
        })

    if request.method == "POST":
        password = request.POST.get("password")
        if password == password_obj.password:
            request.session["settings_authenticated"] = True
            return redirect("settings")
        else:
            messages.error(request, "パスワードが違います")
    return render(request, "portfolio/settings_login.html")

def settings_view(request):
    """設定画面（パスワードチェック付き）"""
    if not request.session.get("settings_authenticated"):
        return redirect("settings_login")
    return render(request, "portfolio/settings.html")

def get_tabs(request):
    """タブ一覧を返すAPI"""
    tabs = list(BottomTab.objects.values())
    return JsonResponse(tabs, safe=False)

@csrf_exempt
def save_tab(request):
    """新しいタブを保存するAPI"""
    if request.method == "POST":
        data = json.loads(request.body)
        tab = BottomTab.objects.create(
            name=data["name"],
            icon=data["icon"],
            url_name=data["url_name"],
            order=data.get("order", 0),
        )
        return JsonResponse({"id": tab.id, "name": tab.name})
    return JsonResponse({"error": "POST only"}, status=400)

@csrf_exempt
def delete_tab(request, tab_id):
    """タブを削除するAPI"""
    try:
        tab = BottomTab.objects.get(id=tab_id)
        tab.delete()
        return JsonResponse({"success": True})
    except BottomTab.DoesNotExist:
        return JsonResponse({"error": "Not found"}, status=404)

def settings_password_edit(request):
    # DBからパスワードを取得
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

    return render(request, "portfolio/settings_password_edit.html", {"form": form})
