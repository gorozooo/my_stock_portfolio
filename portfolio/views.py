from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect
from django.http import JsonResponse, HttpResponseBadRequest
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.db import transaction
from django.utils import timezone
import json

from .models import BottomTab, SettingsPassword, SubMenu, Stock, StockMaster
from .forms import SettingsPasswordForm
from .utils import get_bottom_tabs


# -----------------------------
# 共通コンテキスト
# -----------------------------
def bottom_tabs_context(request):
    return {"BOTTOM_TABS": get_bottom_tabs()}


# -----------------------------
# メイン画面
# -----------------------------
@login_required
def main_view(request):
    return render(request, "main.html")


# -----------------------------
# 認証
# -----------------------------
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


# -----------------------------
# 株関連ページ
# -----------------------------
@login_required
def stock_list_view(request):
    stocks = Stock.objects.all()
    for stock in stocks:
        stock.chart_history = [
            stock.total_cost,
            stock.total_cost * 1.05,
            stock.total_cost * 0.95,
            stock.unit_price,
            stock.unit_price * 1.01,
        ]
        stock.profit_amount = stock.unit_price * stock.shares - stock.total_cost
        stock.profit_rate = round(stock.profit_amount / stock.total_cost * 100, 2) if stock.total_cost else 0
    return render(request, "stock_list.html", {"stocks": stocks})


@login_required
def stock_create(request):
    if request.method == "POST":
        data = request.POST
        Stock.objects.create(
            purchase_date=data.get("purchase_date") or timezone.now().date(),
            ticker=data.get("ticker"),
            name=data.get("name"),
            account_type=data.get("account_type"),
            sector=data.get("sector"),
            shares=int(data.get("shares") or 0),
            unit_price=float(data.get("unit_price") or 0),
            total_cost=float(data.get("total_cost") or 0),
            note=data.get("note", ""),
        )
        return redirect("stock_list")
    return render(request, "stocks/stock_create.html")


@login_required
def cash_view(request):
    return render(request, "cash.html")


@login_required
def realized_view(request):
    return render(request, "realized.html")


@login_required
def trade_history(request):
    return render(request, "trade_history.html")


# -----------------------------
# 設定画面ログイン
# -----------------------------
def settings_login(request):
    password_obj = SettingsPassword.objects.first()
    if not password_obj:
        return render(
            request,
            "settings_login.html",
            {"error": "パスワードが設定されていません。管理画面で作成してください。"},
        )
    if request.method == "POST":
        password = request.POST.get("password") or ""
        if password == password_obj.password:
            request.session["settings_authenticated"] = True
            return redirect("settings")
        messages.error(request, "パスワードが違います")
    return render(request, "settings_login.html")


# -----------------------------
# 設定画面本体
# -----------------------------
@login_required
def settings_view(request):
    if not request.session.get("settings_authenticated"):
        return redirect("settings_login")
    return render(request, "settings.html")


# -----------------------------
# 設定系子ページ
# -----------------------------
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


# -----------------------------
# 設定画面パスワード編集
# -----------------------------
@login_required
def settings_password_edit(request):
    if not request.session.get("settings_authenticated"):
        return redirect("settings_login")

    password_obj = SettingsPassword.objects.first()
    if request.method == "POST":
        form = SettingsPasswordForm(request.POST, instance=password_obj)
        if form.is_valid():
            form.save()
            messages.success(request, "パスワードを更新しました。")
            return redirect("settings_password_edit")
    else:
        form = SettingsPasswordForm(instance=password_obj)

    return render(request, "settings_password_edit.html", {"form": form})


# -----------------------------
# API: タブ一覧
# -----------------------------
@login_required
def get_tabs(request):
    return JsonResponse(get_bottom_tabs(), safe=False)


# -----------------------------
# API: タブ追加／更新
# -----------------------------
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
        tab = BottomTab.objects.filter(id=tab_id).first()
        if not tab:
            return JsonResponse({"error": "Tab not found"}, status=404)
        tab.name, tab.icon, tab.url_name, tab.link_type = name, icon, url_name, link_type
        tab.save()
        tab.submenus.all().delete()
    else:
        max_tab = BottomTab.objects.order_by("-order").first()
        tab = BottomTab.objects.create(
            name=name,
            icon=icon,
            url_name=url_name,
            link_type=link_type,
            order=(max_tab.order + 1) if max_tab else 0,
        )

    for idx, sm in enumerate(submenus):
        tab.submenus.create(
            name=(sm.get("name") or "").strip(),
            url=(sm.get("url") or "").strip(),
            link_type=(sm.get("link_type") or "view").strip(),
            order=idx,
        )

    return JsonResponse({
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
    })


# -----------------------------
# API: タブ削除
# -----------------------------
@csrf_exempt
@require_POST
@transaction.atomic
@login_required
def delete_tab(request, tab_id):
    tab = BottomTab.objects.filter(id=tab_id).first()
    if not tab:
        return JsonResponse({"error": "Tab not found"}, status=404)
    tab.delete()
    return JsonResponse({"success": True})


# -----------------------------
# API: タブ順序保存
# -----------------------------
@csrf_exempt
@require_POST
@transaction.atomic
@login_required
def save_order(request):
    try:
        data = json.loads(request.body or "[]")
    except json.JSONDecodeError:
        return HttpResponseBadRequest("Invalid JSON")

    for item in data:
        tab_id = item.get("id")
        order = item.get("order")
        tab = BottomTab.objects.filter(id=tab_id).first()
        if tab and isinstance(order, int):
            tab.order = order
            tab.save()

    return JsonResponse({"success": True})


# -----------------------------
# API: 証券コード → 銘柄・業種
# -----------------------------
def get_stock_by_code(request):
    code = (request.GET.get("code") or "").strip()
    stock = StockMaster.objects.filter(code=code).first()
    if stock:
        return JsonResponse({"success": True, "name": stock.name, "sector": stock.sector})
    return JsonResponse({"success": False})


# -----------------------------
# API: 銘柄名サジェスト
# -----------------------------
def suggest_stock_name(request):
    q = (request.GET.get("q") or "").strip()
    qs = StockMaster.objects.filter(name__icontains=q)[:10]
    return JsonResponse([
        {"code": s.code, "name": s.name, "sector": s.sector or ""}
        for s in qs
    ], safe=False)

# -----------------------------
# API: 33業種リスト
# -----------------------------
def get_sector_list(request):
    sectors = list(
        StockMaster.objects.values_list("sector", flat=True).distinct()
    )
    return JsonResponse([s or "" for s in sectors], safe=False)

