from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse, HttpResponseBadRequest, HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.db import transaction, models
from django.utils import timezone
import json
import yfinance as yf

from .models import (
    BottomTab,
    SubMenu,
    Stock,
    StockMaster,
    SettingsPassword,
    RealizedProfit
)
from .forms import SettingsPasswordForm
from .utils import get_bottom_tabs
from django.template.loader import get_template


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
    current_page = "ホーム"
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


# views.py
import datetime
import json
from django.db.models import F, Value, Case, When, CharField
from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse, HttpResponse
from django.template.loader import get_template
from django.contrib.auth.decorators import login_required
from django.utils import timezone
from django.views.decorators.http import require_POST

import yfinance as yf

from .models import Stock, RealizedProfit


# -----------------------------
# 株関連ページ
# -----------------------------
@login_required
def stock_list_view(request):
    """
    保有株一覧ページ。
    証券会社（broker）→口座区分（account_type）→銘柄 の二段階グルーピングに対応。
    """

    qs = Stock.objects.all()

    # ---- broker_name の正規化（CharField/choices / FK / 文字列に対応）----
    broker_name_annot = None
    try:
        broker_field = Stock._meta.get_field("broker")
        broker_type = broker_field.get_internal_type()
        if broker_type == "CharField" and getattr(Stock, "BROKER_CHOICES", None):
            whens = [When(broker=code, then=Value(label)) for code, label in Stock.BROKER_CHOICES]
            broker_name_annot = Case(*whens, default=F("broker"), output_field=CharField())
        elif broker_type == "ForeignKey":
            qs = qs.select_related("broker")
            # Brokerモデルに name がある想定（なければ適宜変更）
            broker_name_annot = F("broker__name")
        else:
            broker_name_annot = F("broker")
    except Exception:
        broker_name_annot = Value("（未設定）", output_field=CharField())

    # ---- account_type_name の正規化（CharField/choices / FK / 文字列に対応）----
    account_name_annot = None
    try:
        at_field = Stock._meta.get_field("account_type")
        at_type = at_field.get_internal_type()
        if at_type == "CharField" and getattr(Stock, "ACCOUNT_TYPE_CHOICES", None):
            whens = [When(account_type=code, then=Value(label)) for code, label in Stock.ACCOUNT_TYPE_CHOICES]
            account_name_annot = Case(*whens, default=F("account_type"), output_field=CharField())
        elif at_type == "ForeignKey":
            qs = qs.select_related("account_type")
            account_name_annot = F("account_type__name")
        else:
            account_name_annot = F("account_type")
    except Exception:
        account_name_annot = Value("（未設定）", output_field=CharField())

    qs = qs.annotate(
        broker_name=broker_name_annot,
        account_type_name=account_name_annot,
    ).order_by("broker_name", "account_type_name", "name", "ticker")

    # ---- 現在株価・損益・チャート ----
    for stock in qs:
        try:
            ticker_symbol = f"{stock.ticker}.T"
            ticker = yf.Ticker(ticker_symbol)

            todays = ticker.history(period="1d")
            stock.current_price = float(todays["Close"].iloc[-1]) if not todays.empty else float(stock.unit_price or 0)

            hist = ticker.history(period="1mo")
            ohlc = []
            if not hist.empty:
                for dt, row in hist.iterrows():
                    ohlc.append({
                        "t": dt.strftime("%Y-%m-%d"),
                        "o": round(float(row["Open"]), 2),
                        "h": round(float(row["High"]), 2),
                        "l": round(float(row["Low"]), 2),
                        "c": round(float(row["Close"]), 2),
                    })
            stock.chart_history = ohlc

            shares = int(stock.shares or 0)
            unit_price = float(stock.unit_price or 0)
            current = float(stock.current_price or unit_price)
            stock.total_cost = shares * unit_price
            stock.profit_amount = round(current * shares - stock.total_cost)
            stock.profit_rate = round((stock.profit_amount / stock.total_cost * 100), 2) if stock.total_cost else 0.0

        except Exception as e:
            shares = int(stock.shares or 0)
            unit_price = float(stock.unit_price or 0)
            stock.current_price = unit_price
            stock.total_cost = shares * unit_price
            stock.profit_amount = 0
            stock.profit_rate = 0.0
            stock.chart_history = []
            print(f"Error fetching data for {stock.ticker}: {e}")

        stock.chart_json = json.dumps(stock.chart_history, ensure_ascii=False)

    return render(request, "stock_list.html", {"stocks": qs})

@login_required
def stock_create(request):
    """
    新規登録ビュー。

    【今回の主修正ポイント】
    - position の受け入れ幅を拡張（「買／売」「買い／売り」どちらでもOK）
      → 保存時は「買」「売」に正規化。
    """
    errors = {}
    data = {}

    if request.method == "POST":
        data = request.POST

        # --- 購入日（必須 & 形式チェック YYYY-MM-DD） ---
        purchase_date = None
        purchase_date_str = (data.get("purchase_date") or "").strip()
        if purchase_date_str:
            try:
                purchase_date = datetime.date.fromisoformat(purchase_date_str)
            except ValueError:
                errors["purchase_date"] = "購入日を正しい形式（YYYY-MM-DD）で入力してください"
        else:
            errors["purchase_date"] = "購入日を入力してください"

        # --- 基本項目 ---
        ticker = (data.get("ticker") or "").strip()
        name = (data.get("name") or "").strip()
        account_type = (data.get("account_type") or "").strip()
        broker = (data.get("broker") or "").strip()
        sector = (data.get("sector") or "").strip()
        note = (data.get("note") or "").strip()

        # --- ポジション（必須）: 「買／売」「買い／売り」を受け入れ、保存は「買」「売」に正規化 ---
        pos_raw = (data.get("position") or "").strip()
        normalize_map = {
            "買": "買", "買い": "買",
            "売": "売", "売り": "売",
        }
        position = normalize_map.get(pos_raw)
        if not pos_raw:
            errors["position"] = "ポジションを選択してください"
        elif position is None:
            errors["position"] = "ポジションの値が不正です（買／売 から選択してください）"

        # --- 数値項目 ---
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

        # 取得額（POSTに来ていなければ shares * unit_price で計算）
        try:
            total_cost = (
                float(data.get("total_cost"))
                if data.get("total_cost") not in (None, "",)
                else (shares * unit_price)
            )
        except (TypeError, ValueError):
            total_cost = shares * unit_price

        # --- 必須チェック ---
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

        # --- 保存 ---
        if not errors:
            Stock.objects.create(
                purchase_date=purchase_date,
                ticker=ticker,
                name=name,
                account_type=account_type,
                broker=broker,          # CharField/choices or FK の PK/文字列を期待
                sector=sector,
                position=position,      # ★ 正規化済み（「買」or「売」）
                shares=shares,
                unit_price=unit_price,
                total_cost=total_cost,
                note=note,
            )
            return redirect("stock_list")

    else:
        # 初期表示用データ
        data = {
            "purchase_date": "",
            "ticker": "",
            "name": "",
            "account_type": "",
            "broker": "",
            "sector": "",
            "position": "",     # 初期は未選択
            "shares": "",
            "unit_price": "",
            "total_cost": "",
            "note": "",
        }

    context = {
        "errors": errors,
        "data": data,
        "BROKER_CHOICES": getattr(Stock, "BROKER_CHOICES", ()),
    }

    tpl = get_template("stocks/stock_create.html")
    return HttpResponse(tpl.render(context, request))


@login_required
@require_POST
def sell_stock_view(request, pk):
    """
    売却API相当。既存ロジックを踏襲。
    """
    stock = get_object_or_404(Stock, pk=pk)
    # current_price が未計算の場合は unit_price 代用（安全側）
    current_price = getattr(stock, "current_price", None)
    if current_price is None:
        current_price = stock.unit_price

    total_profit = (float(current_price) - float(stock.unit_price or 0)) * int(stock.shares or 0)

    RealizedProfit.objects.create(
        stock_name=stock.name,
        ticker=stock.ticker,
        shares=stock.shares,
        purchase_price=stock.unit_price,
        sell_price=current_price,
        total_profit=total_profit,
        sold_at=timezone.now()
    )
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

    settings_cards = [
        {"url_name": "tab_manager", "icon": "fa-table-columns", "title": "タブ管理", "description": "下タブやサブメニューを管理", "color": "green", "progress": 80, "badge": "New"},
        {"url_name": "theme_settings", "icon": "fa-paintbrush", "title": "テーマ変更", "description": "画面の色やスタイルを変更", "color": "blue", "progress": 40, "badge": "未設定"},
        {"url_name": "notification_settings", "icon": "fa-bell", "title": "通知設定", "description": "通知のオン／オフを切替", "color": "pink", "progress": 100},
        {"url_name": "settings_password_edit", "icon": "fa-lock", "title": "パスワード変更", "description": "ログインパスワードを変更", "color": "orange", "progress": 50},
    ]
    return render(request, "settings.html", {"settings_cards": settings_cards})


# -----------------------------
# 設定系子ページ
# -----------------------------
@login_required
def tab_manager_view(request):
    if not request.session.get("settings_authenticated"):
        return redirect("settings_login")

    tabs_qs = BottomTab.objects.prefetch_related('submenus').order_by('order', 'id')

    tab_list = []
    for tab in tabs_qs:
        tab_list.append({
            "id": tab.id,
            "name": tab.name,
            "icon": tab.icon or "📌",
            "url_name": tab.url_name,
            "order": tab.order,
            "submenus": [
                {"id": sub.id, "name": sub.name, "url": sub.url, "order": sub.order}
                for sub in tab.submenus.all().order_by('order', 'id')
            ],
        })

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
# API: タブ一覧（下部ナビ用のJSON）
# -----------------------------
@login_required
def get_tabs(request):
    tabs_qs = BottomTab.objects.prefetch_related("submenus").order_by("order", "id")
    data = []
    for tab in tabs_qs:
        data.append({
            "id": tab.id,
            "name": tab.name,
            "icon": tab.icon,
            "url_name": tab.url_name,
            "order": tab.order,
            "submenus": [{"id": sm.id, "name": sm.name, "url": sm.url, "order": sm.order} for sm in tab.submenus.all().order_by("order", "id")]
        })
    return JsonResponse(data, safe=False)


# -----------------------------
# API: タブ追加／更新
# -----------------------------
@csrf_exempt
@require_POST
@login_required
@transaction.atomic
def save_tab(request):
    try:
        data = json.loads(request.body.decode("utf-8"))
    except json.JSONDecodeError:
        return HttpResponseBadRequest("Invalid JSON")

    name = (data.get("name") or "").strip()
    icon = (data.get("icon") or "").strip()
    url_name = (data.get("url_name") or "").strip()
    tab_id = data.get("id")

    if not name:
        return JsonResponse({"error": "タブ名は必須です"}, status=400)

    if tab_id:
        tab = BottomTab.objects.filter(id=tab_id).first()
        if not tab:
            return JsonResponse({"error": "Tab not found"}, status=404)
        tab.name = name
        tab.icon = icon
        tab.url_name = url_name
        tab.save()
    else:
        max_tab = BottomTab.objects.order_by("-order").first()
        tab = BottomTab.objects.create(
            name=name,
            icon=icon,
            url_name=url_name,
            order=(max_tab.order + 1) if max_tab else 0,
        )

    return JsonResponse({
        "id": tab.id,
        "name": tab.name,
        "icon": tab.icon,
        "url_name": tab.url_name,
        "order": tab.order,
        "submenus": [
            {"id": sm.id, "name": sm.name, "url": sm.url, "order": sm.order}
            for sm in tab.submenus.all().order_by("order", "id")
        ],
    })

# -----------------------------
# API: タブ削除
# -----------------------------
@csrf_exempt
@require_POST
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
@login_required
def save_order(request):
    try:
        data = json.loads(request.body.decode("utf-8"))
    except json.JSONDecodeError:
        return HttpResponseBadRequest("Invalid JSON")

    for idx, tab_id in enumerate(data):  # data は配列 [3,1,2,...]
        tab = BottomTab.objects.filter(id=tab_id).first()
        if tab:
            tab.order = idx
            tab.save()
    return JsonResponse({"success": True})

# -----------------------------
# API: サブメニュー保存
# -----------------------------
@csrf_exempt
@require_POST
@login_required
def save_submenu(request):
    try:
        data = json.loads(request.body.decode("utf-8"))
    except json.JSONDecodeError:
        return HttpResponseBadRequest("Invalid JSON")

    tab_id = data.get("tab_id")
    name = (data.get("name") or "").strip()
    url = (data.get("url") or "").strip()

    if not tab_id or not name:
        return JsonResponse({"error": "tab_idと名前は必須です"}, status=400)

    tab = BottomTab.objects.filter(id=tab_id).first()
    if not tab:
        return JsonResponse({"error": "Tab not found"}, status=404)

    submenu_id = data.get("id")
    if submenu_id:
        sm = tab.submenus.filter(id=submenu_id).first()
        if not sm:
            return JsonResponse({"error": "Submenu not found"}, status=404)
        sm.name = name
        sm.url = url
        sm.save()
    else:
        max_order = tab.submenus.aggregate(max_order=models.Max("order"))["max_order"] or 0
        sm = tab.submenus.create(name=name, url=url, order=max_order + 1)

    return JsonResponse({"id": sm.id, "name": sm.name, "url": sm.url, "order": sm.order})

# -----------------------------
# API: サブメニュー削除
# -----------------------------
@csrf_exempt
@require_POST
@login_required
def delete_submenu(request, sub_id):
    sm = SubMenu.objects.filter(id=sub_id).first()
    if not sm:
        return JsonResponse({"error": "Submenu not found"}, status=404)
    sm.delete()
    return JsonResponse({"success": True})

# -----------------------------
# API: サブメニュー順序保存
# -----------------------------
@csrf_exempt
@require_POST
@login_required
def save_submenu_order(request):
    try:
        data = json.loads(request.body.decode("utf-8"))
    except json.JSONDecodeError:
        return HttpResponseBadRequest("Invalid JSON")

    for idx, sub_id in enumerate(data):  # data は配列 [10,11,12]
        sm = SubMenu.objects.filter(id=sub_id).first()
        if sm:
            sm.order = idx
            sm.save()
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
            json_dumps_params={"ensure_ascii": False}
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