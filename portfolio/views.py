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
from django.template.loader import get_template
from django.http import HttpResponse

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
    # 現在ページ名を設定
    current_page = "ホーム"

    # 最終更新日時（例: 今の時刻）
    last_update = timezone.now()

    return render(request, "main.html", {
        "current_page": current_page,
        "last_update": last_update,
    })




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
import yfinance as yf
import json
from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from .models import Stock

@login_required
def stock_list_view(request):
    stocks = Stock.objects.all()

    for stock in stocks:
        try:
            ticker_symbol = f"{stock.ticker}.T"
            ticker = yf.Ticker(ticker_symbol)

            # 最新株価
            todays_data = ticker.history(period="1d")
            stock.current_price = float(todays_data["Close"].iloc[-1]) if not todays_data.empty else stock.unit_price

            # 過去1か月ローソク足
            history = ticker.history(period="1mo")
            ohlc_list = []
            if not history.empty:
                for date, row in history.iterrows():
                    ohlc_list.append({
                        "t": date.strftime("%Y-%m-%d"),
                        "o": round(row["Open"], 2),
                        "h": round(row["High"], 2),
                        "l": round(row["Low"], 2),
                        "c": round(row["Close"], 2),
                    })
            stock.chart_history = ohlc_list

            # 損益
            stock.total_cost = stock.shares * stock.unit_price
            stock.profit_amount = stock.current_price * stock.shares - stock.total_cost
            stock.profit_rate = round(stock.profit_amount / stock.total_cost * 100, 2) if stock.total_cost else 0

        except Exception as e:
            stock.chart_history = []
            stock.current_price = stock.unit_price
            stock.total_cost = stock.shares * stock.unit_price
            stock.profit_amount = 0
            stock.profit_rate = 0
            print(f"Error fetching data for {stock.ticker}: {e}")

    for stock in stocks:
        stock.chart_json = json.dumps(stock.chart_history)

    return render(request, "stock_list.html", {"stocks": stocks})
@login_required
def stock_create(request):
    errors = {}
    data = {}

    if request.method == "POST":
        data = request.POST
        purchase_date = data.get("purchase_date") or timezone.now().date()
        ticker = (data.get("ticker") or "").strip()
        name = (data.get("name") or "").strip()
        account_type = (data.get("account_type") or "").strip()
        broker = (data.get("broker") or "").strip()
        sector = (data.get("sector") or "").strip()
        note = (data.get("note") or "").strip()

        try:
            shares = int(data.get("shares"))
            if shares <= 0:
                errors["shares"] = "株数は1以上を入力してください"
        except (TypeError, ValueError):
            shares = 0
            errors["shares"] = "株数を正しく入力してください"

        try:
            unit_price = float(data.get("unit_price"))
            if unit_price < 0:
                errors["unit_price"] = "取得単価は0以上を入力してください"
        except (TypeError, ValueError):
            unit_price = 0
            errors["unit_price"] = "取得単価を正しく入力してください"

        total_cost = float(data.get("total_cost") or (shares * unit_price))

        if not ticker:
            errors["ticker"] = "証券コードを入力してください"
        if not name:
            errors["name"] = "銘柄名を入力してください"
        if not account_type:
            errors["account_type"] = "口座区分を選択してください"
        if not broker:
            errors["broker"] = "証券会社を選択してください"
        if not sector:
            errors["sector"] = "セクターを入力してください"

        if not errors:
            Stock.objects.create(
                purchase_date=purchase_date,
                ticker=ticker,
                name=name,
                account_type=account_type,
                broker=broker,
                sector=sector,
                shares=shares,
                unit_price=unit_price,
                total_cost=total_cost,
                note=note,
            )
            return redirect("stock_list")
    else:
        data = {
            "purchase_date": "",
            "ticker": "",
            "name": "",
            "account_type": "",
            "broker": "",
            "sector": "",
            "shares": "",
            "unit_price": "",
            "total_cost": "",
            "note": "",
        }

    context = {
        "errors": errors,
        "data": data,
        "BROKER_CHOICES": Stock.BROKER_CHOICES,  # ← テンプレに渡す
    }

    # どのテンプレートを読んでいるかログに出す
    tpl = get_template("stocks/stock_create.html")
    print(">>> USING TEMPLATE:", getattr(getattr(tpl, "origin", None), "name", tpl))

    return HttpResponse(tpl.render(context, request))

from django.shortcuts import get_object_or_404
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.contrib.auth.decorators import login_required
from .models import Stock, RealizedProfit
from django.utils import timezone

@login_required
@require_POST
def sell_stock_view(request, pk):
    stock = get_object_or_404(Stock, pk=pk)

    # 損益計算
    total_profit = (stock.current_price - stock.unit_price) * stock.shares

    # 実現損益レコード作成
    RealizedProfit.objects.create(
        stock_name=stock.name,
        ticker=stock.ticker,
        shares=stock.shares,
        purchase_price=stock.unit_price,
        sell_price=stock.current_price,
        total_profit=total_profit,
        sold_at=timezone.now()
    )

    # 株をDBから削除
    stock.delete()

    return JsonResponse({"status": "ok"})
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
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required

from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from .models import Tab

@login_required
def tab_manager_view(request):
    # セッション認証チェック
    if not request.session.get("settings_authenticated"):
        return redirect("settings_login")

    # -------------------- DBからタブとサブメニューを取得 --------------------
    # ログインユーザーのタブを取得、サブメニューもまとめて取得
    tabs = Tab.objects.filter(user=request.user).prefetch_related('submenus').order_by('id')

    # テンプレートに渡すため辞書形式に変換
    tab_list = []
    for tab in tabs:
        tab_list.append({
            "id": tab.id,
            "name": tab.name,
            "icon": tab.icon or "📌",  # アイコンが空ならデフォルト
            "submenus": [{"id": sub.id, "name": sub.name} for sub in tab.submenus.all()]
        })

    # -------------------- レンダリング --------------------
    return render(request, "tab_manager.html", {"tabs": tab_list})


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
        return JsonResponse(
            {"success": True, "name": stock.name, "sector": stock.sector},
            json_dumps_params={"ensure_ascii": False}  # ←日本語をUnicodeエスケープせず返す
        )
    return JsonResponse({"success": False}, json_dumps_params={"ensure_ascii": False})


# -----------------------------
# API: 銘柄名サジェスト
# -----------------------------
def suggest_stock_name(request):
    q = (request.GET.get("q") or "").strip()
    qs = StockMaster.objects.filter(name__icontains=q)[:10]
    data = [
        {"code": s.code, "name": s.name, "sector": s.sector or ""}
        for s in qs
    ]
    return JsonResponse(data, safe=False, json_dumps_params={"ensure_ascii": False})


# -----------------------------
# API: 33業種リスト
# -----------------------------
def get_sector_list(request):
    sectors = list(
        StockMaster.objects.values_list("sector", flat=True).distinct()
    )
    return JsonResponse([s or "" for s in sectors], safe=False, json_dumps_params={"ensure_ascii": False})
    
# views.py
def settings_view(request):
    settings_cards = [
        {"url_name": "tab_manager", "icon": "fa-table-columns", "title": "タブ管理", "description": "下タブやサブメニューを管理", "color":"green", "progress": 80, "badge":"New"},
        {"url_name": "theme_settings", "icon": "fa-paintbrush", "title": "テーマ変更", "description": "画面の色やスタイルを変更", "color":"blue", "progress": 40, "badge":"未設定"},
        {"url_name": "notification_settings", "icon": "fa-bell", "title": "通知設定", "description": "通知のオン／オフを切替", "color":"pink", "progress": 100},
        {"url_name": "settings_password_edit", "icon": "fa-lock", "title": "パスワード変更", "description": "ログインパスワードを変更", "color":"orange", "progress": 50},
    ]
    return render(request, "settings.html", {"settings_cards": settings_cards})