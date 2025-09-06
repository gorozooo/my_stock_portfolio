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

        # 売却日（テンプレの <input type="date" name="sell_date"> から）
        sell_date_str = (request.POST.get("sell_date") or "").strip()
        sold_at = timezone.now()
        if sell_date_str:
            try:
                # 売却日の 15:00（日本の大引け相当）で保存 ※必要なら任意の時刻に調整
                sell_date = datetime.date.fromisoformat(sell_date_str)
                sold_at_naive = datetime.datetime.combine(sell_date, datetime.time(15, 0, 0))
                sold_at = timezone.make_aware(sold_at_naive, timezone.get_current_timezone())
            except Exception:
                # 日付パースに失敗しても致命ではない（エラー表示にしてもOK）
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
            # current_price が妥当ならそれを優先、無ければ unit_price
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
        total_profit = (float(price) - unit_price) * shares_to_sell     # 概算損益（参考値）
        fee = estimated_amount - float(actual_profit or 0.0)            # 指定の式で算出（負値になり得る場合もそのまま保存）

        # --- RealizedProfit へ記録 ---
        rp_kwargs = dict(
            stock_name=stock.name,
            ticker=stock.ticker,
            shares=shares_to_sell,
            purchase_price=unit_price,
            sell_price=float(price),
            total_profit=actual_profit if actual_profit != 0.0 else total_profit,  # 「実際の損益額」があればそれを優先保存
            sold_at=sold_at,
        )
        # fee フィールドが存在すれば追加（無ければ無視）
        try:
            RealizedProfit._meta.get_field("fee")
            rp_kwargs["fee"] = fee
        except Exception:
            pass
        # 参考：estimated_amount を保存したい場合はモデルにフィールド追加の上で同様に対応
        # try:
        #     RealizedProfit._meta.get_field("estimated_amount")
        #     rp_kwargs["estimated_amount"] = estimated_amount
        # except Exception:
        #     pass

        RealizedProfit.objects.create(**rp_kwargs)

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
        stock.account = request.POST.get("account") or stock.account
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
@cache_page(60)
def stock_price_json(request, pk: int):
    """
    価格タブ用の軽量JSON:
      - 直近30営業日の終値時系列（ミニチャート用）
      - 52週高値/安値、最新終値、前日比
    ネットワーク失敗時は、DBの current_price で最低限を返す
    """
    stock = get_object_or_404(Stock, pk=pk)

    # yfinance シンボル（モデルに合わせた正規化関数があるなら流用）
    ticker = Stock.to_yf_symbol(stock.ticker) if hasattr(Stock, "to_yf_symbol") else stock.ticker
    today = timezone.localdate()
    start_1m = today - dt.timedelta(days=60)   # 30営業日程度入るように余裕を取る
    start_52w = today - dt.timedelta(days=400) # 52週用の余裕

    series = []
    last_close = None
    prev_close = None
    high_52w = None
    low_52w = None

    try:
        tkr = yf.Ticker(ticker)

        # ミニチャート用：Closeだけ抜く
        hist_1m = tkr.history(start=start_1m.isoformat(), end=(today+dt.timedelta(days=1)).isoformat())
        if not hist_1m.empty:
            closes = hist_1m["Close"].dropna()
            # 時系列（最大30点に間引き）
            pts = list(closes.items())[-30:]
            series = [{"t": str(idx.date()), "c": float(val)} for idx, val in pts]
            if len(closes) >= 2:
                last_close = float(closes.iloc[-1])
                prev_close = float(closes.iloc[-2])
            elif len(closes) == 1:
                last_close = float(closes.iloc[-1])

        # 52週高安
        hist_52w = tkr.history(start=start_52w.isoformat(), end=(today+dt.timedelta(days=1)).isoformat())
        if not hist_52w.empty:
            high_52w = float(hist_52w["High"].dropna().max())
            low_52w  = float(hist_52w["Low"].dropna().min())

    except Exception:
        pass  # ネットワークなどは無視してフォールバックへ

    # フォールバック（最低限の表示を保証）
    if last_close is None or last_close <= 0:
        last_close = float(stock.current_price or stock.unit_price or 0.0)
    if prev_close is None:
        prev_close = last_close

    change = last_close - prev_close
    change_pct = (change / prev_close * 100.0) if prev_close else 0.0

    data = {
        "series": series,             # [{t: 'YYYY-MM-DD', c: 1234.5}, ...] 最大30点
        "last_close": last_close,     # 最新終値
        "prev_close": prev_close,     # 前終値
        "change": change,             # 前日比
        "change_pct": change_pct,     # 前日比%
        "high_52w": high_52w,         # 52週高値（取れなかったら null）
        "low_52w": low_52w,           # 52週安値（取れなかったら null）
    }
    return JsonResponse(data)

# 先頭付近の import に以下が無ければ追加してください
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

@cache_page(60)
@login_required
@require_GET
def stock_fundamental_json(request, pk: int):
    stock = get_object_or_404(Stock, pk=pk)

    ticker = Stock.to_yf_symbol(stock.ticker) if hasattr(Stock, "to_yf_symbol") else (stock.ticker or "")
    result = {
        "per": None,
        "pbr": None,
        "div_yield_pct": None,   # ← 最終的に「%値」を入れる（3.10 なら 3.10）
        "market_cap": None,
        "eps_est": None,
        "source_updated": None,
    }

    last_price = float(stock.current_price or stock.unit_price or 0.0)

    if yf and ticker:
        try:
            tkr = yf.Ticker(ticker)
            fi = getattr(tkr, "fast_info", {}) or {}
            try:
                info = tkr.info if isinstance(getattr(tkr, "info", None), dict) else {}
            except Exception:
                info = {}

            per = (fi.get("trailingPE") or info.get("trailingPE") or
                   fi.get("forwardPE")  or info.get("forwardPE"))
            pbr = (fi.get("priceToBook") or info.get("priceToBook"))

            # ====== ここを修正：dividendYield のスケールを正規化 ======
            raw_div = fi.get("dividendYield", None)
            if raw_div is None:
                raw_div = info.get("dividendYield", None)

            div_pct = None
            if raw_div is not None:
                try:
                    y = float(raw_div)
                    # 0 < y <= 1.0 なら 0.031 → 3.1 とみなして ×100
                    # 1.0 < y（例: 3.1）なら そのまま%値として採用
                    # 100 を超えるような明らかな異常は 1/100 して救済
                    if y <= 0:
                        div_pct = None
                    elif y <= 1.0:
                        div_pct = y * 100.0
                    elif y > 100.0:
                        div_pct = y / 100.0
                    else:
                        div_pct = y
                except Exception:
                    div_pct = None
            result["div_yield_pct"] = div_pct
            # ================================================

            mcap = (fi.get("marketCap") or info.get("marketCap"))

            last = (fi.get("last_price") or fi.get("lastPrice") or info.get("currentPrice"))
            if last and (not last_price or last_price <= 0):
                last_price = float(last)

            eps_est = None
            try:
                if per and last_price and float(per) > 0:
                    eps_est = float(last_price) / float(per)
            except Exception:
                eps_est = None

            def f(x):
                try:
                    v = float(x)
                    if math.isfinite(v):
                        return v
                except Exception:
                    pass
                return None

            result.update({
                "per": f(per),
                "pbr": f(pbr),
                "market_cap": f(mcap),
                "eps_est": f(eps_est),
                "source_updated": timezone.now().isoformat(timespec="seconds"),
            })

        except Exception:
            result["source_updated"] = timezone.now().isoformat(timespec="seconds")
    else:
        result["source_updated"] = timezone.now().isoformat(timespec="seconds")

    return JsonResponse(result)

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