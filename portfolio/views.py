from __future__ import annotations

from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse, HttpResponseBadRequest, HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.db import transaction, models
from django.utils import timezone
from django.views.decorators.cache import cache_page
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
import re

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
# ---------------------------------------
# スマホファースト想定 / HTML・CSS・JS 分離前提
# 目的：
#  - broker → account_type → 銘柄 の二段階グループ化で表示
#  - broker/account_type が CharField(choices) / FK / 素の文字列 いずれでも表示が壊れない
#  - 現在株価・損益のみ計算（チャートは取得/埋め込みしない）
#  - 価格はDjangoキャッシュで15分キャッシュ
# ---------------------------------------

import datetime
import logging

from django.core.cache import cache
from django.db.models import F, Value, Case, When, CharField
from django.http import JsonResponse, HttpResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.template.loader import get_template
from django.contrib.auth.decorators import login_required
from django.utils import timezone
from django.views.decorators.http import require_POST

import yfinance as yf  # 現在株価の軽量取得に使用

from .models import Stock, RealizedProfit

logger = logging.getLogger(__name__)

# 価格キャッシュの有効期限（秒）
PRICE_CACHE_TTL = 15 * 60  # 15分
# -----------------------------
# 株関連ページ
# -----------------------------
@login_required
def stock_list_view(request):
    
    # ----保有株一覧ページ。
    #- 証券会社（broker）→口座区分（account_type）→銘柄 の二段階グルーピングに対応
    #- broker/account_type は CharField(choices) / ForeignKey / 文字列 どれでも正しく表示
    # - 現在株価・損益のみを計算してテンプレへ渡す（チャートは取得しない）
    #- yfinance 結果はキャッシュしてレスポンスを高速化

    # ---- ベースQuerySet（userフィールドがあればユーザーで絞り込み） ----
    qs = Stock.objects.all()
    try:
        field_names = {f.name for f in Stock._meta.get_fields()}
        if "user" in field_names:
            qs = qs.filter(user=request.user)
    except Exception as e:
        logger.debug("User filter not applied: %s", e)

    # ---- broker_name の正規化 ----
    try:
        broker_field = Stock._meta.get_field("broker")
        broker_type = broker_field.get_internal_type()
        if broker_type == "CharField" and getattr(Stock, "BROKER_CHOICES", None):
            whens = [When(broker=code, then=Value(label)) for code, label in Stock.BROKER_CHOICES]
            broker_name_annot = Case(*whens, default=F("broker"), output_field=CharField())
        elif broker_type == "ForeignKey":
            qs = qs.select_related("broker")
            # Brokerモデルの表示名フィールド。必要に応じて変更（例: display_name 等）
            broker_name_annot = F("broker__name")
        else:
            broker_name_annot = F("broker")
    except Exception as e:
        logger.warning("broker_name annotate fallback: %s", e)
        broker_name_annot = Value("（未設定）", output_field=CharField())

    # ---- account_type_name の正規化 ----
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
    except Exception as e:
        logger.warning("account_type_name annotate fallback: %s", e)
        account_name_annot = Value("（未設定）", output_field=CharField())

    # ---- 表示名注釈 + 規定順ソート ----
    qs = qs.annotate(
        broker_name=broker_name_annot,
        account_type_name=account_name_annot,
    ).order_by("broker_name", "account_type_name", "name", "ticker")

    # ---- 現在株価・損益の計算（チャートは取得しない）----
    for stock in qs:
        stock.current_price = _get_current_price_cached(stock.ticker, fallback=stock.unit_price)

        shares = int(stock.shares or 0)
        unit_price = float(stock.unit_price or 0)
        current = float(stock.current_price or unit_price)

        stock.total_cost = shares * unit_price
        stock.profit_amount = round(current * shares - stock.total_cost)
        stock.profit_rate = round((stock.profit_amount / stock.total_cost * 100), 2) if stock.total_cost else 0.0

    return render(request, "stock_list.html", {"stocks": qs})


def _get_current_price_cached(ticker: str, fallback: float = 0.0) -> float:
    """
    yfinance の当日終値を取得し、Djangoキャッシュに保存/取得する。
    取得失敗時は fallback（通常は取得単価）を返す。
    """
    if not ticker:
        return float(fallback or 0.0)

    cache_key = f"price:{ticker}"
    cached = cache.get(cache_key)
    if isinstance(cached, (int, float)):
        return float(cached)

    # 日本株のYahoo Financeシンボル（例: 7203.T）
    symbol = f"{ticker}.T"
    try:
        t = yf.Ticker(symbol)
        todays = t.history(period="1d")
        if not todays.empty:
            price = float(todays["Close"].iloc[-1])
            cache.set(cache_key, price, PRICE_CACHE_TTL)
            return price
        else:
            # データ空ならフォールバック
            cache.set(cache_key, float(fallback or 0.0), PRICE_CACHE_TTL)
            return float(fallback or 0.0)
    except Exception as e:
        logger.info("Price fetch failed for %s: %s", symbol, e)
        cache.set(cache_key, float(fallback or 0.0), PRICE_CACHE_TTL)
        return float(fallback or 0.0)


@login_required
def stock_create(request):
    """
    新規登録（POST）
    - position を「買」/「売」に正規化
    - 必須/数値チェックを実施
    """
    errors = {}
    data = {}

    if request.method == "POST":
        data = request.POST

        # --- 購入日 ---
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

        # --- ポジション（買い/売り/買/売 を許容） ---
        position = (data.get("position") or "").strip()
        if not position:
            errors["position"] = "ポジションを選択してください"
        elif position not in ("買い", "売り", "買", "売"):
            errors["position"] = "ポジションの値が不正です（買い／売りから選択してください）"

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
            unit_price = 0.0
            errors["unit_price"] = "取得単価を正しく入力してください"

        # 取得額（POSTが空なら shares * unit_price）
        try:
            total_cost = float(data.get("total_cost")) if data.get("total_cost") not in (None, "",) else (shares * unit_price)
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
            normalized_position = "買" if position in ("買", "買い") else "売"

            create_kwargs = dict(
                purchase_date=purchase_date,
                ticker=ticker,
                name=name,
                account_type=account_type,
                broker=broker,
                sector=sector,
                position=normalized_position,
                shares=shares,
                unit_price=unit_price,
                total_cost=total_cost,
                note=note,
            )

            # userフィールドが存在する場合は紐付け
            try:
                if "user" in {f.name for f in Stock._meta.get_fields()}:
                    create_kwargs["user"] = request.user
            except Exception:
                pass

            Stock.objects.create(**create_kwargs)
            return redirect("stock_list")

    else:
        # 初期表示用
        data = {
            "purchase_date": "",
            "ticker": "",
            "name": "",
            "account_type": "",
            "broker": "",
            "sector": "",
            "position": "",
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
def sell_stock_page(request, pk):
    """
    売却専用ページ（市場/指値、部分売却対応）
    - GET:
        ページ表示（現在値が空なら yfinance で軽く取得を試行）
    - POST:
        バリデーション → RealizedProfit へ記録
        手数料 = 概算売却額 - 実際の損益額（未入力なら 0）
        全量売却: Stock を削除 / 部分売却: shares 減算 + total_cost 再計算
    """
    stock = get_object_or_404(Stock, pk=pk)
    errors = []

    # --- GET 時の現在値（未設定なら軽く取得） ---
    current_price_for_view = float(stock.current_price or 0.0)
    if current_price_for_view <= 0:
        try:
            symbol = f"{stock.ticker}.T" if not str(stock.ticker).endswith(".T") else stock.ticker
            todays = yf.Ticker(symbol).history(period="1d")
            if not todays.empty:
                current_price_for_view = float(todays["Close"].iloc[-1])
        except Exception:
            current_price_for_view = 0.0  # 取得失敗時は 0 のまま（テンプレ側で単価を使って概算）

    # --- ティッカー等から 4桁の証券コードを抽出する小ヘルパー ---
    def extract_securities_code(ticker_or_code: str) -> str:
        """
        例: '7203.T' -> '7203', '8306' -> '8306', 'AAPL' -> ''（抽出不可）
        """
        if not ticker_or_code:
            return ""
        s = str(ticker_or_code).strip()
        # 末尾の .T / .JP など拡張子を削除
        s = re.sub(r'\.[A-Za-z]+$', '', s)
        m = re.match(r'^(\d{4})', s)
        return m.group(1) if m else ""

    if request.method == "POST":
        mode = (request.POST.get("sell_mode") or "").strip()

        # 売却株数
        try:
            shares_to_sell = int(request.POST.get("shares") or 0)
        except (TypeError, ValueError):
            shares_to_sell = 0

        # 指値（limit のとき）
        try:
            limit_price = float(request.POST.get("limit_price") or 0)
        except (TypeError, ValueError):
            limit_price = 0.0

        # 売却日（テンプレの <input type="date" name="sell_date">）
        sell_date_str = (request.POST.get("sell_date") or "").strip()
        sold_at = timezone.now()
        if sell_date_str:
            try:
                # 売却日の 15:00 に設定（必要あれば調整OK）
                sell_date = datetime.date.fromisoformat(sell_date_str)
                sold_at_naive = datetime.datetime.combine(sell_date, datetime.time(15, 0, 0))
                sold_at = timezone.make_aware(sold_at_naive, timezone.get_current_timezone())
            except Exception:
                errors.append("売却日が不正です。YYYY-MM-DD 形式で指定してください。")

        # 実際の損益額（ユーザー入力）
        try:
            actual_profit_input = request.POST.get("actual_profit", "")
            actual_profit = float(actual_profit_input) if actual_profit_input != "" else 0.0
        except (TypeError, ValueError):
            actual_profit = 0.0
            errors.append("実際の損益額は数値で入力してください。")

        # --- 基本バリデーション ---
        if mode not in ("market", "limit"):
            errors.append("売却方法が不正です。")

        if shares_to_sell <= 0:
            errors.append("売却株数を 1 以上で指定してください。")
        elif shares_to_sell > int(stock.shares or 0):
            errors.append("保有株数を超える売却はできません。")

        # 売却価格（1株あたり）
        price = None
        if mode == "market":
            price = float(stock.current_price or current_price_for_view or stock.unit_price or 0)
        else:  # limit
            if limit_price <= 0:
                errors.append("指値価格を正しく入力してください。")
            else:
                price = limit_price

        if not price or price <= 0:
            errors.append("売却価格が不正です。")

        # バリデーション NG → 再表示
        if errors:
            return render(
                request,
                "stocks/sell_stock_page.html",
                {
                    "stock": stock,
                    "errors": errors,
                    "current_price": current_price_for_view or 0.0,
                },
            )

        # --- 計算 ---
        unit_price = float(stock.unit_price or 0)
        estimated_amount = float(price) * shares_to_sell                # 概算売却額（手数料控除前の想定）
        total_profit_est = (float(price) - unit_price) * shares_to_sell # 概算損益（参考値）
        fee = estimated_amount - float(actual_profit or 0.0)            # 指定の式（負値もそのまま保存OK）

        # 保存する損益額（実入力があれば優先）
        final_profit_amount = actual_profit if actual_profit != 0.0 else total_profit_est

        # 損益率（%）を可能なら算出（分母=取得総額）
        profit_rate_val = None
        denom = unit_price * shares_to_sell
        if denom:
            profit_rate_val = round((final_profit_amount / denom) * 100, 2)

        # --- 証券コード / 証券会社 / 口座区分 を決定 ---
        posted_code = (request.POST.get("code") or request.POST.get("ticker") or "").strip()
        stock_code    = getattr(stock, "code", "") or ""
        ticker_code   = extract_securities_code(getattr(stock, "ticker", "") or "")
        final_code    = posted_code or stock_code or ticker_code or ""

        # broker / account_type は POST 優先 → 無ければ stock の値
        posted_broker = (request.POST.get("broker") or "").strip()
        posted_acct   = (request.POST.get("account_type") or "").strip()
        final_broker  = posted_broker or getattr(stock, "broker", "") or ""
        final_account = posted_acct   or getattr(stock, "account_type", "") or ""

        # --- RealizedProfit へ記録（モデルの実フィールド名に合わせる） ---
        RealizedProfit.objects.create(
            user=request.user,                         # user を必ず紐付け（モデルが null=True でも可）
            date=sold_at.date(),                       # sold_at(aware) → date だけ保存
            stock_name=stock.name,
            code=final_code,
            broker=final_broker,
            account_type=final_account,
            trade_type="sell",

            quantity=shares_to_sell,
            purchase_price=int(round(unit_price)) if unit_price else None,
            sell_price=int(round(price)) if price else None,
            fee=int(round(fee)) if fee else None,

            profit_amount=int(round(final_profit_amount)) if final_profit_amount is not None else None,
            profit_rate=profit_rate_val,               # DecimalField だが float 入力でもDjangoが変換
        )

        # --- 在庫調整（部分売却対応） ---
        remaining = int(stock.shares or 0) - shares_to_sell
        if remaining <= 0:
            stock.delete()
        else:
            stock.shares = remaining
            # total_cost は平均単価ベースで按分しない（要件に合わせて計算式を変える）
            stock.total_cost = int(round(remaining * unit_price))
            stock.save(update_fields=["shares", "total_cost", "updated_at"])

        return redirect("stock_list")

    # --- GET 表示 ---
    return render(
        request,
        "stocks/sell_stock_page.html",
        {
            "stock": stock,
            "errors": errors,
            "current_price": current_price_for_view or 0.0,
        },
    )
        
from django.shortcuts import render, get_object_or_404, redirect
from django.views.decorators.http import require_http_methods
from .models import Stock

@require_http_methods(["GET", "POST"])
def edit_stock_page(request, pk):
    stock = get_object_or_404(Stock, pk=pk)
    if request.method == "POST":
        stock.shares = int(request.POST.get("shares") or stock.shares)
        stock.unit_price = float(request.POST.get("unit_price") or stock.unit_price)
        stock.account_type = request.POST.get("account_type") or stock.account
        stock.position = request.POST.get("position") or stock.position
        stock.save()
        return redirect("stock_list")
    # 専用ページはベースレイアウトで _edit_form.html を読み込む
    return render(request, "stocks/edit_page.html", {"stock": stock})

def edit_stock_fragment(request, pk):
    """モーダルで読み込む“フォームだけ”の部分HTMLを返す"""
    stock = get_object_or_404(Stock, pk=pk)
    return render(request, "stocks/edit_form.html", {"stock": stock})

from django.http import JsonResponse
from django.template.loader import render_to_string
from django.views.decorators.http import require_GET

@login_required
@require_GET
def stock_detail_fragment(request, pk: int):
    """
    詳細モーダルのHTML断片（タブの器＋ボタン類）。最初は「概要」タブだけ中身を動的に入れる。
    """
    stock = get_object_or_404(Stock, pk=pk)
    html = render_to_string("stocks/_detail_modal.html", {"stock": stock}, request=request)
    # フロントはこのHTMLをそのままDOMに挿入して使う
    return HttpResponse(html)

@login_required
@require_GET
def stock_overview_json(request, pk: int):
    """
    概要タブの軽量JSON。
    - DB値を返すが、from_card_current が来ていて > 0 の場合は current_price をそれで上書き
    - 取得額/評価額/損益も一貫計算
    """
    stock = get_object_or_404(Stock, pk=pk)

    # カード側で見えている現在株価（data-current_price）を優先的に採用
    from_card = request.GET.get("from_card_current")
    try:
        from_card_val = float(from_card) if from_card is not None else 0.0
    except (TypeError, ValueError):
        from_card_val = 0.0

    # ベースはDB
    shares = int(stock.shares or 0)
    unit_price = float(stock.unit_price or 0)
    db_current = float(stock.current_price or 0)
    current_price = from_card_val if from_card_val > 0 else db_current

    # 取得額（保険で再計算）
    total_cost = float(stock.total_cost or (shares * unit_price))

    # 評価額と損益（買い/売りで式が異なる）
    market_value = current_price * shares
    if stock.position == "売り":
        profit_loss = (unit_price - current_price) * shares
    else:
        profit_loss = market_value - total_cost

    data = {
        "id": stock.id,
        "name": stock.name,
        "ticker": stock.ticker,
        "broker": stock.broker,
        "account_type": stock.account_type,
        "position": stock.position,
        "purchase_date": stock.purchase_date.isoformat() if stock.purchase_date else None,
        "shares": shares,
        "unit_price": unit_price,
        "current_price": current_price,  # ← ここがカード値で上書きされる
        "total_cost": total_cost,
        "market_value": market_value,
        "profit_loss": profit_loss,
        "note": stock.note or "",
        "updated_at": stock.updated_at.isoformat() if stock.updated_at else None,
    }
    return JsonResponse(data)

from django.views.decorators.http import require_GET
from django.http import JsonResponse
from django.contrib.auth.decorators import login_required
import datetime as dt
from django.utils import timezone
import yfinance as yf

@login_required
@require_GET
@cache_page(60 * 1440)  # 1日キャッシュ
def stock_price_json(request, pk: int):
    """
    価格タブ用の軽量JSON:
      - OHLC 時系列（ローソク足用／なければ Close のみ）
        ※ ?period=1M/3M/1Y で切替（既定 1M）
      - 最新終値・前日比
      - 52週高値/安値
      - 上場来高値/安値
    失敗時は DB の current_price などでフォールバック
    """
    stock = get_object_or_404(Stock, pk=pk)
    ticker = Stock.to_yf_symbol(stock.ticker) if hasattr(Stock, "to_yf_symbol") else stock.ticker

    # ---- 期間決定（デフォルト 1M）----
    period_q = (request.GET.get("period") or "1M").upper()
    if period_q not in ("1M", "3M", "1Y"):
        period_q = "1M"

    today = timezone.localdate()

    # 期間に応じた開始日（暦日で余裕を広めに確保）
    if period_q == "1M":
        start_range = today - dt.timedelta(days=60)   # 30営業日程度入るよう余裕
        cap_points = 30
    elif period_q == "3M":
        start_range = today - dt.timedelta(days=150)
        cap_points = 60
    else:  # "1Y"
        start_range = today - dt.timedelta(days=430)
        cap_points = 260

    start_52w = today - dt.timedelta(days=400)

    series = []        # ローソク足: [{t, o, h, l, c}] / ライン: [{t, c}]
    last_close = None
    prev_close = None
    high_52w = None
    low_52w = None
    high_all = None
    low_all = None

    try:
        tkr = yf.Ticker(ticker)

        # --- 指定期間の時系列（1日足） ---
        hist = tkr.history(
            start=start_range.isoformat(),
            end=(today + dt.timedelta(days=1)).isoformat(),
            interval="1d",
        )

        if not hist.empty:
            # yfinance は列名: ["Open","High","Low","Close", ...]
            # NaN を除外して尾部 cap_points 件に間引き
            df = hist[["Open", "High", "Low", "Close"]].dropna()
            if not df.empty:
                tail = df.tail(cap_points)
                # OHLC で返す（JS は o/h/l/c があればローソク足、無ければライン）
                series = [
                    {
                        "t": str(idx.date()),
                        "o": float(row["Open"]),
                        "h": float(row["High"]),
                        "l": float(row["Low"]),
                        "c": float(row["Close"]),
                    }
                    for idx, row in tail.iterrows()
                ]

                closes = df["Close"]
                if len(closes) >= 2:
                    last_close = float(closes.iloc[-1])
                    prev_close = float(closes.iloc[-2])
                elif len(closes) == 1:
                    last_close = float(closes.iloc[-1])

        # --- 52週高安 ---
        hist_52w = tkr.history(
            start=start_52w.isoformat(),
            end=(today + dt.timedelta(days=1)).isoformat(),
            interval="1d",
        )
        if not hist_52w.empty:
            hh = hist_52w["High"].dropna()
            ll = hist_52w["Low"].dropna()
            if not hh.empty:
                high_52w = float(hh.max())
            if not ll.empty:
                low_52w = float(ll.min())

        # --- 上場来高安 ---
        hist_all = tkr.history(period="max", interval="1d")
        if not hist_all.empty:
            hh_all = hist_all["High"].dropna()
            ll_all = hist_all["Low"].dropna()
            if not hh_all.empty:
                high_all = float(hh_all.max())
            if not ll_all.empty:
                low_all = float(ll_all.min())

    except Exception:
        # ネットワーク・レート制限などは無視してフォールバック
        pass

    # フォールバック（最低限の表示）
    if not last_close or last_close <= 0:
        last_close = float(stock.current_price or stock.unit_price or 0.0)
    if not prev_close or prev_close <= 0:
        prev_close = last_close

    change = last_close - prev_close
    change_pct = (change / prev_close * 100.0) if prev_close else 0.0

    # もし何らかの理由で OHLC を作れなかったら、Close だけの series を生成（上限 cap_points）
    if not series:
        # Close だけの簡易 series（最新日付だけでも形を合わせる）
        # ※ ここではラベル用に today から cap_points 逆算して日付を並べるなどもできるが、
        #    余計な誤解を避けるため空配列のまま返す/または 1点だけ返す方が安全。
        # ここでは空配列のまま返す（JS 側は防御済みで描画をスキップ）
        series = []

    data = {
        "period": period_q,          # クライアント側の整合確認用
        "series": series,            # [{t:'YYYY-MM-DD', o,h,l,c}] or [{t, c}]
        "last_close": last_close,
        "prev_close": prev_close,
        "change": change,
        "change_pct": change_pct,
        "high_52w": high_52w,
        "low_52w": low_52w,
        "high_all": high_all,        # 上場来高値
        "low_all": low_all,          # 上場来安値
    }
    return JsonResponse(data)
    
from django.views.decorators.cache import cache_page
from django.http import JsonResponse, Http404
from django.shortcuts import render, get_object_or_404
from django.utils import timezone
import datetime as dt
import math

try:
    import yfinance as yf
except Exception:
    yf = None

# views.py
from django.http import JsonResponse
from django.utils import timezone
from django.contrib.auth.decorators import login_required
from django.views.decorators.cache import cache_page
from django.views.decorators.http import require_GET
from django.shortcuts import get_object_or_404
import math

try:
    import yfinance as yf
except Exception:
    yf = None

# portfolio/views.py
import datetime as dt
import yfinance as yf
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_GET
from django.views.decorators.cache import cache_page

from .models import Stock

@cache_page(60 * 1440)  # 1日キャッシュ（必要に応じて）
@login_required
@require_GET
def stock_fundamental_json(request, pk: int):
    """
    指標タブ用の軽量JSON:
      - PER, PBR, 時価総額, EPS
      - 配当利回り(%), 予想配当(1株あたり, 円)
    優先順:
      1) fast_info / info
      2) TTM配当合計から推計
      3) カードの現在値(from_card_current)やDB値で補完
    返す値は「数値そのもの」。フロントで整形して表示します。
    """
    stock = get_object_or_404(Stock, pk=pk)
    symbol = Stock.to_yf_symbol(stock.ticker) if hasattr(Stock, "to_yf_symbol") else stock.ticker

    # まずはカード側の現在値を受け取り、無ければDB
    last_price = None
    try:
        cp = request.GET.get("from_card_current")
        if cp:
            last_price = float(cp)
    except Exception:
        pass
    if not last_price:
        try:
            last_price = float(stock.current_price or 0.0)
        except Exception:
            last_price = 0.0

    per = None
    pbr = None
    eps = None
    mcap = None
    div_yield_pct = None   # 例: 3.1（= 3.1%）
    dps = None             # 1株あたり配当（円想定）
    updated = timezone.now().isoformat()

    try:
        tkr = yf.Ticker(symbol)

        # --- fast_info 優先（軽量・高速）
        fi = getattr(tkr, "fast_info", {}) or {}
        def fi_get(key):
            try:
                # fast_info は dict風 or 属性風どちらもあり得る
                if isinstance(fi, dict):
                    return fi.get(key, None)
                return getattr(fi, key, None)
            except Exception:
                return None

        lp = fi_get("last_price")
        if lp:
            last_price = float(lp)

        # 利回り: fast_info.dividend_yield は「小数（0.031）」のことが多い
        dy = fi_get("dividend_yield")
        if dy is not None:
            try:
                dyf = float(dy)
                # 1未満なら小数→%に換算、すでに%（>1）ならそのまま
                div_yield_pct = dyf * 100.0 if dyf < 1 else dyf
            except Exception:
                pass

        # PER
        trpe = fi_get("trailing_pe")
        if trpe:
            try: per = float(trpe)
            except Exception: pass

        # 時価総額
        fmc = fi_get("market_cap")
        if fmc:
            try: mcap = float(fmc)
            except Exception: pass

        # --- info で補完（重い場合あり）
        info = {}
        try:
            info = tkr.info or {}
        except Exception:
            info = {}

        if per is None:
            v = info.get("trailingPE")
            if v: 
                try: per = float(v)
                except Exception: pass

        if pbr is None:
            v = info.get("priceToBook")
            if v:
                try: pbr = float(v)
                except Exception: pass

        if eps is None:
            v = info.get("trailingEps")
            if v:
                try: eps = float(v)
                except Exception: pass

        if mcap is None:
            v = info.get("marketCap")
            if v:
                try: mcap = float(v)
                except Exception: pass

        # 予想配当（1株）: info['dividendRate'] を最優先
        v = info.get("dividendRate")
        if v:
            try: dps = float(v)
            except Exception: pass

        # 配当利回りが無くて、TTM配当から推計
        if (div_yield_pct is None or dps is None):
            try:
                divs = tkr.dividends  # pandas Series
                if divs is not None and not divs.empty:
                    since = timezone.now().date() - dt.timedelta(days=400)
                    ttm = divs[divs.index.date >= since]
                    if not ttm.empty:
                        ttm_sum = float(ttm.sum())
                        # DPS 未取得なら TTM を近似として採用
                        if dps is None:
                            dps = ttm_sum
                        # 利回りも価格が取れていれば計算
                        if div_yield_pct is None and last_price:
                            div_yield_pct = (ttm_sum / float(last_price)) * 100.0
            except Exception:
                pass

        # まだ DPS が無いが、利回りと価格があるなら逆算
        if dps is None and div_yield_pct is not None and last_price:
            dps = (float(div_yield_pct) / 100.0) * float(last_price)

    except Exception:
        # yfinance 側失敗は無視してフォールバックのみ
        pass

    # 価格フォールバック
    if not last_price:
        try:
            last_price = float(stock.current_price or stock.unit_price or 0.0)
        except Exception:
            last_price = 0.0

    # マイナス等の不正値は None に正規化
    def clean_num(x):
        try:
            f = float(x)
            if f != f:  # NaN
                return None
            return f
        except Exception:
            return None

    data = {
        "ticker": stock.ticker,
        "last_price": clean_num(last_price),
        "per": clean_num(per),
        "pbr": clean_num(pbr),
        "eps": clean_num(eps),
        "market_cap": clean_num(mcap),
        "dividend_yield_pct": clean_num(div_yield_pct),   # 3.1 のように %そのもの
        "dividend_per_share": clean_num(dps),             # 円想定（数値）
        "updated_at": updated,
    }
    return JsonResponse(data)
    
# portfolio/views.py
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_GET
from django.views.decorators.cache import cache_page
import datetime as dt
import yfinance as yf

from .models import Stock

def _to_yf_symbol(ticker: str) -> str:
    return Stock.to_yf_symbol(ticker) if hasattr(Stock, "to_yf_symbol") else ticker



import datetime as dt
from typing import Any, Dict, List, Tuple, TypedDict, Union, Optional

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ImproperlyConfigured
from django.http import JsonResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.cache import cache_page
from django.views.decorators.http import require_GET

from .models import Stock  # 必須
# DBフォールバック用（存在すれば使う）
try:
    from .models import StockNews  # Optional: stock ごとのニュースを保存している場合
    HAS_STOCK_NEWS_MODEL = True
except Exception:
    HAS_STOCK_NEWS_MODEL = False


# ---- optional: 外部ニュースプロバイダ（実装済みなら使う） ---------------------
# app/services/news_provider.py に fetch_stock_news があれば優先利用します。
# 署名:
#   fetch_stock_news(stock: Stock, page: int, limit: int, lang: str) -> Tuple[List[Dict[str, Any]], Optional[int]]
# 返り値:
#   (items, total) を想定。total 不明なら None。
FETCH_PROVIDER = None
try:
    from app.services.news_provider import fetch_stock_news as _fetch_from_provider  # type: ignore
    FETCH_PROVIDER = _fetch_from_provider  # prefer external provider if available
except Exception:
    FETCH_PROVIDER = None


# ---- schema 用の型（lint/補完用） --------------------------------------------
class NewsItemDict(TypedDict, total=False):
    id: str
    title: str
    url: str
    source: str
    published_at: str     # ISO8601 UTC (Z)
    summary: str
    sentiment: str        # "pos" | "neg" | "neu"
    impact: int           # 1..3
    date: str             # YYYY-MM-DD (ローカル日付 or 市場日付など)


# ---- ユーティリティ -----------------------------------------------------------
MIN_LIMIT = 10
MAX_LIMIT = 50
DEFAULT_LIMIT = 20


def _parse_positive_int(value: str, default: int, min_v: int, max_v: int) -> int:
    try:
        v = int(value)
    except Exception:
        return default
    if v < min_v:
        return min_v
    if v > max_v:
        return max_v
    return v


def _to_utc_isoz(dt_obj: dt.datetime) -> str:
    """aware datetime を UTC 'YYYY-MM-DDTHH:MM:SSZ' にして返す"""
    if timezone.is_naive(dt_obj):
        dt_obj = timezone.make_aware(dt_obj, timezone.get_current_timezone())
    dt_utc = dt_obj.astimezone(timezone.utc)
    # Python 3.11+: timespec='seconds' で秒まで固定
    return dt_utc.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _as_date_string(dt_obj: dt.datetime, *, tz: Optional[dt.tzinfo] = None) -> str:
    """チャートマーキング用の日付（YYYY-MM-DD）。デフォルトは現在タイムゾーン。"""
    if timezone.is_naive(dt_obj):
        aware = timezone.make_aware(dt_obj, timezone.get_current_timezone())
    else:
        aware = dt_obj
    if tz is None:
        tz = timezone.get_current_timezone()
    local_dt = aware.astimezone(tz)
    return local_dt.date().isoformat()


def _sanitize_url(url: Optional[str]) -> str:
    if not url:
        return ""
    # ここでは簡易チェックのみ（http/https）
    if url.startswith("http://") or url.startswith("https://"):
        return url
    return ""


def _serialize_from_dict(d: Dict[str, Any]) -> NewsItemDict:
    # 外部プロバイダが返す dict を標準スキーマに寄せる
    title = str(d.get("title") or "").strip()
    source = str(d.get("source") or "").strip()
    pub = d.get("published_at")
    if isinstance(pub, dt.datetime):
        published_at = _to_utc_isoz(pub)
        date_str = _as_date_string(pub)
    else:
        # 文字列想定（UTC/Z入り等）。パースに失敗したら now。
        try:
            # 可能なら fromisoformat で簡易対応
            parsed = dt.datetime.fromisoformat(str(pub).replace("Z", "+00:00"))
        except Exception:
            parsed = timezone.now()
        if timezone.is_naive(parsed):
            parsed = timezone.make_aware(parsed, timezone.utc)
        published_at = _to_utc_isoz(parsed)
        date_str = _as_date_string(parsed)

    out: NewsItemDict = {
        "id": str(d.get("id") or ""),
        "title": title or "（タイトル不明）",
        "url": _sanitize_url(d.get("url")),
        "source": source or "—",
        "published_at": published_at,
        "summary": str(d.get("summary") or ""),
        "sentiment": str(d.get("sentiment") or "neu"),
        "impact": int(d.get("impact") or 1),
        "date": str(d.get("date") or date_str),
    }
    return out


def _serialize_from_model(m: Any) -> NewsItemDict:
    # DBモデル（StockNews）から標準スキーマへ
    # 想定フィールド: id, title, url, source, published_at (DateTimeField), summary, sentiment, impact
    title = (getattr(m, "title", "") or "").strip()
    source = (getattr(m, "source", "") or "").strip()
    published_at = getattr(m, "published_at", None) or timezone.now()
    return {
        "id": str(getattr(m, "id", "")),
        "title": title or "（タイトル不明）",
        "url": _sanitize_url(getattr(m, "url", "") or ""),
        "source": source or "—",
        "published_at": _to_utc_isoz(published_at),
        "summary": getattr(m, "summary", "") or "",
        "sentiment": getattr(m, "sentiment", "") or "neu",
        "impact": int(getattr(m, "impact", 1) or 1),
        "date": _as_date_string(published_at),
    }


def _fetch_from_db(stock: Stock, page: int, limit: int, lang: str) -> Tuple[List[NewsItemDict], int, bool]:
    """
    DBに StockNews がある場合のフォールバック取得。
    total は count で返す。has_more はページングから算出。
    """
    if not HAS_STOCK_NEWS_MODEL:
        return ([], 0, False)

    qs = StockNews.objects.filter(stock=stock).order_by("-published_at")
    # lang 列がある場合はコメントアウト解除:
    # if hasattr(StockNews, "lang") and lang != "all":
    #     qs = qs.filter(lang=lang)

    total = qs.count()
    start = (page - 1) * limit
    end = start + limit
    objs = list(qs[start:end])
    items = [_serialize_from_model(m) for m in objs]
    has_more = end < total
    return (items, total, has_more)


@login_required
@require_GET
@cache_page(300)  # 5分キャッシュ
def stock_news_json(request, pk: int):
    """
    ニュースJSON（lazy-load用）
    GET:
      page: 1.. (default=1)
      limit: 10..50 (default=20)
      lang: all/jp/us（拡張用）
    返却:
      {
        "page": 1,
        "limit": 20,
        "has_more": true/false,
        "total": 123,  # 不明なら省略
        "items": [NewsItemDict, ...]
      }
    """
    stock = get_object_or_404(Stock, pk=pk)

    # --- 入力
    page = _parse_positive_int(request.GET.get("page", "1"), default=1, min_v=1, max_v=10_000)
    limit = _parse_positive_int(request.GET.get("limit", str(DEFAULT_LIMIT)), DEFAULT_LIMIT, MIN_LIMIT, MAX_LIMIT)
    lang = (request.GET.get("lang") or "all").lower()

    # --- 取得（外部プロバイダ or DB フォールバック）
    items: List[NewsItemDict] = []
    total: Optional[int] = None
    has_more: bool = False

    # 1) 外部プロバイダ優先
    if FETCH_PROVIDER:
        try:
            raw_items, provider_total = FETCH_PROVIDER(stock=stock, page=page, limit=limit, lang=lang)
            items = [_serialize_from_dict(d) for d in (raw_items or [])]
            total = provider_total if isinstance(provider_total, int) and provider_total >= 0 else None
            # has_more は total が分かれば計算、分からなければ「items が limit に満たない場合 False、満たしていれば True」と推定
            if total is not None:
                has_more = page * limit < total
            else:
                has_more = len(items) >= limit
        except Exception as e:
            # 外部失敗時は DB フォールバック
            items, total_db, has_more = _fetch_from_db(stock, page, limit, lang)
            total = total_db if total_db > 0 else None
    else:
        # 2) DB フォールバック
        items, total_db, has_more = _fetch_from_db(stock, page, limit, lang)
        total = total_db if total_db > 0 else None

    # --- レスポンス整形
    payload: Dict[str, Any] = {
        "page": page,
        "limit": limit,
        "has_more": has_more,
        "items": items,
    }
    if total is not None:
        payload["total"] = total

    return JsonResponse(payload, json_dumps_params={"ensure_ascii": False})    
@login_required
def cash_view(request):
    return render(request, "cash.html")

import datetime
import re
import yfinance as yf
from collections import OrderedDict

from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone

# -----------------------------
# 損益一覧
# -----------------------------
from .models import Stock, RealizedProfit
@login_required
def realized_view(request):
    """
    実現損益一覧ページ
    DBの RealizedProfit をログインユーザーごとに取得し、
    年月ごとにグループ化してテンプレートに渡す
    """
    # ユーザーの実現損益を取得（最新日付順）
    qs = RealizedProfit.objects.filter(user=request.user).order_by('-date', '-id')

    # 年月ごとにまとめる（例: "2025-08"）
    groups = OrderedDict()
    for t in qs:
        ym = t.date.strftime('%Y-%m')
        groups.setdefault(ym, []).append(t)

    # テンプレートへ渡す
    return render(request, "realized.html", {
        "rows_by_ym": groups,        # ← ループ用
    })
@login_required
def trade_history(request):
    return render(request, "trade_history.html")

# -----------------------------
# 配当入力
# -----------------------------
# 追加/確認：上の方のimport
from datetime import date
from django.shortcuts import render, redirect
from django.urls import reverse
from django.contrib import messages
# Dividendモデルを使っているなら
from .models import Dividend

def dividend_new_page(request):
    """
    配当入力（スマホファースト）
    テンプレは ルート直下: templates/dividend_form.html を使用
    """
    if request.method == "POST":
        ticker       = (request.POST.get("ticker") or "").strip()
        stock_name   = (request.POST.get("stock_name") or "").strip()
        received_at  = request.POST.get("received_at") or str(date.today())
        gross_amount = int(request.POST.get("gross_amount") or 0)
        tax          = int(request.POST.get("tax") or 0)
        account_type = (request.POST.get("account_type") or "").strip()
        broker       = (request.POST.get("broker") or "").strip()
        memo         = (request.POST.get("memo") or "").strip()

        if not ticker or not stock_name or gross_amount <= 0:
            messages.error(request, "必須項目（銘柄名・コード・配当金）を入力してください。")
            # ↓ エラー時も必ずテンプレを返す（return None防止）
            ctx = {
                "init": {
                    "ticker": ticker,
                    "stock_name": stock_name,
                    "account_type": account_type,
                    "broker": broker,
                    "received_at": received_at,
                }
            }
            return render(request, "dividend_form.html", ctx)

        # 保存
        Dividend.objects.create(
            ticker=ticker,
            stock_name=stock_name,
            received_at=received_at,
            gross_amount=gross_amount,
            tax=tax,
            account_type=account_type,
            broker=broker,
            memo=memo,
        )
        messages.success(request, "配当を登録しました。")

        # 戻り先（あなたのURL名に合わせて必要なら変更）
        try:
            return redirect(reverse("realized"))
        except Exception:
            try:
                return redirect(reverse("realized_trade_list"))
            except Exception:
                return redirect(reverse("stock_list"))

    # GET: 初期表示（必ずrenderを返す）
    ctx = {
        "init": {
            "ticker":       request.GET.get("ticker", ""),
            "stock_name":   request.GET.get("stock_name", ""),
            "account_type": request.GET.get("account_type", ""),
            "broker":       request.GET.get("broker", ""),
            "received_at":  request.GET.get("received_at", "") or str(date.today()),
        }
    }
    return render(request, "dividend_form.html", ctx)   
    
# -----------------------------
# 登録ページ
# -----------------------------
from django.contrib.auth.decorators import login_required
from django.urls import reverse

@login_required
def register_hub(request):
    settings_cards = [
        {
            "url_name": "stock_create",
            "color": "#3AA6FF",
            "icon": "fa-circle-plus",
            "title": "新規登録",
            "description": "保有株を素早く追加",
            "badge": "推奨",         # 任意
            "progress": None,        # 任意
        },
        {
            "url_name": "dividend_new",
            "color": "#00C48C",
            "icon": "fa-coins",
            "title": "配当入力",
            "description": "受取配当を記録",
            "badge": None,
            "progress": None,
        },
        {
            "url_name": "cashflow_create",
            "color": "#FF8A3D",
            "icon": "fa-wallet",
            "title": "入出金",
            "description": "入金/出金を登録",
            "badge": None,
            "progress": None,
        },
    ]
    return render(request, "register_hub.html", {"settings_cards": settings_cards})
    
# -----------------------------
# 入出金
# -----------------------------
from django.shortcuts import render
from django.contrib.auth.decorators import login_required

@login_required
def cashflow_create(request):
    # ダミーなので実際の処理はまだしない
    if request.method == "POST":
        # ここでフォーム処理予定
        return render(request, "cashflow_success.html")

    # GET時はフォーム画面（ダミー）
    return render(request, "cashflow_form.html")
    
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