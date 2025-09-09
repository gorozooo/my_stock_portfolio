# portfolio/views.py
from __future__ import annotations

# ===== 標準 =====
from collections import OrderedDict, defaultdict
from datetime import date, datetime, timedelta
import logging
import re
from typing import Dict, List, Optional, Tuple

# ===== Django =====
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.db import models
from django.db.models import (
    Sum, F, Value, Case, When, CharField, IntegerField, Q,
)
from django.db.models.functions import TruncMonth
from django.http import (
    JsonResponse, HttpResponseBadRequest, HttpResponse, Http404,
)
from django.shortcuts import render, redirect, get_object_or_404
from django.template.loader import get_template, render_to_string
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import cache_page
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST, require_GET, require_http_methods

# ===== 外部 =====
import yfinance as yf  # ネット環境・制限の可能性に注意

# ===== アプリ =====
from .forms import SettingsPasswordForm
from .models import (
    BottomTab,
    SubMenu,
    Stock,
    StockMaster,
    SettingsPassword,
    RealizedProfit,
    CashFlow,
)
# Dividend が存在する前提で利用。存在しない環境でも落ちないように try。
try:
    from .models import Dividend
    HAS_DIVIDEND = True
except Exception:
    Dividend = None  # type: ignore
    HAS_DIVIDEND = False

from .utils import get_bottom_tabs

logger = logging.getLogger(__name__)

# =============================================================================
# 共通コンテキスト
# =============================================================================
def bottom_tabs_context(request):
    return {"BOTTOM_TABS": get_bottom_tabs()}


# =============================================================================
# 認証
# =============================================================================
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


# =============================================================================
# ユーティリティ：数値・日付
# =============================================================================
def _parse_date_yyyy_mm_dd(s: str) -> date:
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except Exception:
        return timezone.localdate()


def _safe_int(x, default=0) -> int:
    try:
        return int(x)
    except Exception:
        return default


def _safe_float(x, default=0.0) -> float:
    try:
        return float(x)
    except Exception:
        return default


def _extract_securities_code(ticker_or_code: str) -> str:
    """
    '7203.T' -> '7203', '8306' -> '8306', 'AAPL' -> ''
    """
    if not ticker_or_code:
        return ""
    s = str(ticker_or_code).strip()
    s = re.sub(r"\.[A-Za-z]+$", "", s)  # .T などを削除
    m = re.match(r"^(\d{4})", s)
    return m.group(1) if m else ""


# =============================================================================
# ユーティリティ：価格キャッシュ
# =============================================================================
PRICE_CACHE_TTL = 15 * 60  # 15分

def _yf_symbol(ticker: str) -> str:
    """日本株前提の簡易 .T 付与（既に拡張子付きならそのまま）"""
    if not ticker:
        return ""
    if re.search(r"\.[A-Za-z]+$", ticker):
        return ticker
    return f"{ticker}.T"

def _get_current_price_cached(ticker: str, fallback: float = 0.0) -> float:
    if not ticker:
        return float(fallback or 0.0)
    cache_key = f"price:{ticker}"
    cached = cache.get(cache_key)
    if isinstance(cached, (int, float)):
        return float(cached)

    symbol = _yf_symbol(ticker)
    try:
        t = yf.Ticker(symbol)
        todays = t.history(period="1d")
        if not todays.empty:
            price = float(todays["Close"].iloc[-1])
            cache.set(cache_key, price, PRICE_CACHE_TTL)
            return price
        cache.set(cache_key, float(fallback or 0.0), PRICE_CACHE_TTL)
        return float(fallback or 0.0)
    except Exception as e:
        logger.info("Price fetch failed for %s: %s", symbol, e)
        cache.set(cache_key, float(fallback or 0.0), PRICE_CACHE_TTL)
        return float(fallback or 0.0)


# =============================================================================
# メイン（ホーム）ページ
#   - あなたの main.html / main.css / main.js に対応
#   - brokers: rakuten/matsui/sbi のタブ構造
#   - recent_activities: range=7/30/90/all 対応
# =============================================================================

BROKER_TABS: List[Tuple[str, str]] = [
    ("rakuten", "楽天証券"),
    ("matsui",  "松井証券"),
    ("sbi",     "SBI証券"),
]
BROKER_MAP = dict(BROKER_TABS)

@login_required
def main_page(request):
    """
    Topダッシュボード（未来的ガラスUI版）に必要な集計をサーバ側で整形
    """
    user = request.user

    # ---------- 現金残高 ----------
    cash_agg = (
        CashFlow.objects.filter().values("broker", "flow_type").annotate(total=Sum("amount"))
    )
    cash_total = 0
    broker_cash: Dict[str, int] = {k: 0 for k, _ in BROKER_TABS}
    for row in cash_agg:
        b = row["broker"]; t = row["flow_type"]; v = int(row["total"] or 0)
        if b in broker_cash:
            broker_cash[b] += v if t == "in" else -v
        cash_total += v if t == "in" else -v

    # ---------- 保有株（評価・含み損益） ----------
    stocks_qs = Stock.objects.all()
    # userフィールドがあれば絞り込み
    try:
        if "user" in {f.name for f in Stock._meta.get_fields()}:
            stocks_qs = stocks_qs.filter(user=user)
    except Exception:
        pass

    # 口座/証券会社名の正規化（CharField choices / FK / 生文字列のいずれにも対応）
    def _normalize_field(model, field_name: str, fk_label: str = "name"):
        try:
            fld = model._meta.get_field(field_name)
            typ = fld.get_internal_type()
            if typ == "CharField" and getattr(model, f"{field_name.upper()}_CHOICES", None):
                choices = getattr(model, f"{field_name.upper()}_CHOICES")
                whens = [When(**{field_name: code, "then": Value(label)}) for code, label in choices]
                return Case(*whens, default=F(field_name), output_field=CharField())
            if typ == "ForeignKey":
                return F(f"{field_name}__{fk_label}")
            return F(field_name)
        except Exception:
            return Value("（未設定）", output_field=CharField())

    broker_name_annot = _normalize_field(Stock, "broker")
    account_name_annot = _normalize_field(Stock, "account_type")

    # 注釈＋並び
    stocks_qs = stocks_qs.annotate(
        broker_name=broker_name_annot,
        account_type_name=account_name_annot,
    ).order_by("broker_name", "account_type_name", "name", "ticker")

    # 株式集計
    portfolio_value = 0.0
    unrealized_pl = 0.0

    # ブローカー別: 保有銘柄数、時価評価、含み損益、トップポジション、直近イベント
    broker_blocks: Dict[str, dict] = {
        k: {
            "key": k,
            "label": BROKER_MAP[k],
            "balance": broker_cash.get(k, 0),
            "holdings_count": 0,
            "market_value": 0.0,
            "unrealized_pl": 0.0,
            "top_positions": [],  # [{ticker,name,shares,market_value}]
            "recent": [],         # recent_activities() から後で詰める
        } for k, _ in BROKER_TABS
    }

    # ティッカー別の現在値を取得し、評価額と含み損益を計算
    for s in stocks_qs:
        current = _get_current_price_cached(s.ticker, fallback=s.unit_price or 0)
        shares = int(s.shares or 0)
        unit   = float(s.unit_price or 0)

        mv = current * shares
        portfolio_value += mv

        # 含み損益（売りは反転）
        if s.position == "売り":
            pl = (unit - current) * shares
        else:
            pl = mv - (shares * unit)
        unrealized_pl += pl

        # ブローカー別
        bkey = getattr(s, "broker", None)
        if bkey in broker_blocks:
            broker_blocks[bkey]["holdings_count"] += 1
            broker_blocks[bkey]["market_value"] += mv
            broker_blocks[bkey]["unrealized_pl"] += pl
            broker_blocks[bkey]["top_positions"].append({
                "ticker": s.ticker,
                "name": s.name,
                "shares": shares,
                "market_value": mv,
            })

    # トップポジションは評価額順で上位5件
    for k in broker_blocks.keys():
        tops = sorted(broker_blocks[k]["top_positions"], key=lambda x: x["market_value"], reverse=True)[:5]
        broker_blocks[k]["top_positions"] = tops

    # 総資産
    total_assets = int(round(portfolio_value + cash_total))

    # 前日比（簡易：当日・前日の終値合計差。データ無い場合は 0）
    # ※本格的にやるなら履歴テーブルを設ける
    day_change = 0

    # スパークライン（資産推移CSV）：実データが無ければ空文字
    asset_history_csv = ""  # 例: "1000000,1003000,1001000,1010000"

    # 目標資産（リングの最大値）。未設定なら total をそのまま最大とし、リングがフルに光る
    target_assets = 0

    # 実現損益（今月/今年/累計）
    today = timezone.localdate()
    first_of_month = today.replace(day=1)
    first_of_year = date(today.year, 1, 1)

    realized_qs = RealizedProfit.objects.filter(user=user)
    realized_pl_mtd = int(realized_qs.filter(date__gte=first_of_month).aggregate(x=Sum("profit_amount"))["x"] or 0)
    realized_pl_ytd = int(realized_qs.filter(date__gte=first_of_year).aggregate(x=Sum("profit_amount"))["x"] or 0)
    realized_pl_total = int(realized_qs.aggregate(x=Sum("profit_amount"))["x"] or 0)

    # 証券会社ごとの直近アクティビティ（最大10件）
    for k in broker_blocks.keys():
        broker_blocks[k]["recent"] = _recent_activities(user=user, broker=k, days=30, limit=10)

    # brokers: テンプレの期待形式に合わせてリスト化（順序保持）
    brokers = [broker_blocks[k] for k, _ in BROKER_TABS]

    # グローバル最近のアクティビティ（range=7/30/90/all）
    rng = (request.GET.get("range") or "7").lower()
    since_days = {"7": 7, "30": 30, "90": 90}.get(rng)
    recent_activities = _recent_activities(user=user, broker=None, days=since_days, limit=100)

    ctx = dict(
        total_assets=total_assets,
        day_change=day_change,
        portfolio_value=int(round(portfolio_value)),
        cash_total=int(round(cash_total)),
        unrealized_pl=int(round(unrealized_pl)),
        asset_history_csv=asset_history_csv,
        target_assets=target_assets,
        brokers=brokers,
        realized_pl_mtd=realized_pl_mtd,
        realized_pl_ytd=realized_pl_ytd,
        realized_pl_total=realized_pl_total,
        recent_activities=recent_activities,
    )
    return render(request, "main.html", ctx)


def _recent_activities(*, user, broker: Optional[str], days: Optional[int], limit: int) -> List[dict]:
    """
    売買(RealizedProfit)・配当(Dividend)・現金(CashFlow)をまとめた簡易タイムライン。
    broker を指定するとその証券会社のみ。
    days=None なら全期間。
    """
    items: List[dict] = []
    since_date = None
    if days:
        since_date = timezone.localdate() - timedelta(days=days)

    # 売買
    rp = RealizedProfit.objects.filter(user=user)
    if broker:
        rp = rp.filter(broker=broker)
    if since_date:
        rp = rp.filter(date__gte=since_date)
    rp = rp.order_by("-date", "-id")[:limit]
    for r in rp:
        amt = int(r.profit_amount or 0)
        items.append({
            "kind": "trade",
            "kind_label": "売買",
            "date": r.date,
            "ticker": getattr(r, "code", ""),
            "name": r.stock_name,
            "pnl": amt,
            "amount": abs(amt),
            "sign": "+" if amt >= 0 else "-",
            "broker_label": BROKER_MAP.get(getattr(r, "broker", ""), getattr(r, "broker", "")),
            "flow": "",
            "memo": "",
        })

    # 配当
    if HAS_DIVIDEND:
        dv = Dividend.objects.all()
        if hasattr(Dividend, "user"):
            dv = dv.filter(user=user)
        if broker:
            dv = dv.filter(broker=broker)
        if since_date:
            dv = dv.filter(received_at__gte=since_date)
        dv = dv.order_by("-received_at", "-id")[:limit]
        for d in dv:
            net = int(getattr(d, "net_amount", 0) or (int(d.gross_amount or 0) - int(d.tax or 0)))
            items.append({
                "kind": "dividend",
                "kind_label": "配当",
                "date": d.received_at,
                "ticker": getattr(d, "ticker", ""),
                "name": getattr(d, "stock_name", ""),
                "net": net,
                "amount": net,
                "sign": "+",
                "broker_label": BROKER_MAP.get(getattr(d, "broker", ""), getattr(d, "broker", "")),
                "flow": "",
                "memo": getattr(d, "memo", ""),
            })

    # 現金
    cf = CashFlow.objects.all()
    if broker:
        cf = cf.filter(broker=broker)
    if since_date:
        cf = cf.filter(occurred_at__gte=since_date)
    cf = cf.order_by("-occurred_at", "-id")[:limit]
    for c in cf:
        is_in = (c.flow_type == "in")
        items.append({
            "kind": "cash",
            "kind_label": "現金",
            "date": c.occurred_at,
            "ticker": "",
            "name": "",
            "amount": int(c.amount or 0),
            "sign": "+" if is_in else "-",
            "broker_label": BROKER_MAP.get(c.broker, c.broker),
            "flow": "in" if is_in else "out",
            "memo": c.memo or "",
        })

    # 日付降順で統合 → 上位 limit 件
    items.sort(key=lambda x: (x["date"], x.get("ticker", ""), x.get("name", "")), reverse=True)
    return items[:limit]


# =============================================================================
# 保有株一覧（2段グループ：broker → account_type）
# =============================================================================
@login_required
def stock_list_view(request):
    qs = Stock.objects.all()
    try:
        if "user" in {f.name for f in Stock._meta.get_fields()}:
            qs = qs.filter(user=request.user)
    except Exception:
        pass

    # 正規化注釈
    def _norm(model, field, fk_label="name"):
        try:
            fld = model._meta.get_field(field)
            typ = fld.get_internal_type()
            if typ == "CharField" and getattr(model, f"{field.upper()}_CHOICES", None):
                choices = getattr(model, f"{field.upper()}_CHOICES")
                whens = [When(**{field: code, "then": Value(label)}) for code, label in choices]
                return Case(*whens, default=F(field), output_field=CharField())
            if typ == "ForeignKey":
                return F(f"{field}__{fk_label}")
            return F(field)
        except Exception:
            return Value("（未設定）", output_field=CharField())

    qs = qs.annotate(
        broker_name=_norm(Stock, "broker"),
        account_type_name=_norm(Stock, "account_type"),
    ).order_by("broker_name", "account_type_name", "name", "ticker")

    # 軽量な現在値+損益計算
    for s in qs:
        s.current_price = _get_current_price_cached(s.ticker, fallback=s.unit_price)
        sh = int(s.shares or 0)
        up = float(s.unit_price or 0)
        cur = float(s.current_price or up)
        s.total_cost = sh * up
        s.profit_amount = round(cur * sh - s.total_cost)
        s.profit_rate = round((s.profit_amount / s.total_cost * 100), 2) if s.total_cost else 0.0

    return render(request, "stock_list.html", {"stocks": qs})


# =============================================================================
# 実現損益（売買 + 配当）一覧
# =============================================================================
@login_required
def realized_view(request):
    # 売買
    trades_qs = RealizedProfit.objects.filter(user=request.user).order_by("-date", "-id")
    trade_rows = []
    for t in trades_qs:
        trade_rows.append({
            "date": t.date,
            "stock_name": t.stock_name,
            "code": getattr(t, "code", None),
            "broker": t.broker,
            "account_type": t.account_type,
            "trade_type": "sell",
            "quantity": getattr(t, "quantity", None),
            "profit_amount": getattr(t, "profit_amount", 0),
            "profit_rate": getattr(t, "profit_rate", None),
            "purchase_price": getattr(t, "purchase_price", None),
            "sell_price": getattr(t, "sell_price", None),
            "fee": getattr(t, "fee", None),
            "id": t.id,
            "_kind": "trade",
        })

    # 配当
    div_rows = []
    if HAS_DIVIDEND:
        dv_qs = Dividend.objects.all()
        if hasattr(Dividend, "user"):
            dv_qs = dv_qs.filter(user=request.user)
        dv_qs = dv_qs.order_by("-received_at", "-id")
        for d in dv_qs:
            div_rows.append({
                "date": d.received_at,
                "stock_name": d.stock_name,
                "code": getattr(d, "ticker", None),
                "broker": d.broker,
                "account_type": d.account_type,
                "trade_type": "dividend",
                "quantity": None,
                "profit_amount": getattr(d, "net_amount", 0) or (int(d.gross_amount or 0) - int(d.tax or 0)),
                "profit_rate": None,
                "purchase_price": None,
                "sell_price": None,
                "fee": None,
                "id": d.id,
                "_kind": "dividend",
            })

    merged = trade_rows + div_rows
    merged.sort(key=lambda r: (r["date"], r["id"]), reverse=True)

    groups = OrderedDict()
    for row in merged:
        ym = f"{row['date'].year:04d}-{row['date'].month:02d}"
        groups.setdefault(ym, []).append(row)

    totals = {
        "count": len(merged),
        "sum_profit": sum((r["profit_amount"] or 0) for r in merged),
        "sum_profit_only": sum((r["profit_amount"] or 0) for r in merged if (r["profit_amount"] or 0) > 0),
        "sum_loss_only": sum((r["profit_amount"] or 0) for r in merged if (r["profit_amount"] or 0) < 0),
    }

    return render(request, "realized.html", {"rows_by_ym": groups, "totals": totals})


# =============================================================================
# 配当入力
# =============================================================================
@login_required
def dividend_new_page(request):
    if not HAS_DIVIDEND:
        raise Http404("Dividend model not found.")

    if request.method == "POST":
        ticker       = (request.POST.get("ticker") or "").strip()
        stock_name   = (request.POST.get("stock_name") or "").strip()
        received_at  = request.POST.get("received_at") or str(date.today())
        gross_amount = _safe_int(request.POST.get("gross_amount"), 0)
        tax          = _safe_int(request.POST.get("tax"), 0)
        account_type = (request.POST.get("account_type") or "").strip()
        broker       = (request.POST.get("broker") or "").strip()
        memo         = (request.POST.get("memo") or "").strip()

        if not ticker or not stock_name or gross_amount <= 0:
            messages.error(request, "必須項目（銘柄名・コード・配当金）を入力してください。")
            ctx = {"init": {
                "ticker": ticker, "stock_name": stock_name,
                "account_type": account_type, "broker": broker,
                "received_at": received_at,
            }}
            return render(request, "dividend_form.html", ctx)

        kwargs = dict(
            ticker=ticker,
            stock_name=stock_name,
            received_at=received_at,
            gross_amount=gross_amount,
            tax=tax,
            account_type=account_type,
            broker=broker,
            memo=memo,
        )
        if hasattr(Dividend, "user"):
            kwargs["user"] = request.user

        Dividend.objects.create(**kwargs)
        messages.success(request, "配当を登録しました。")

        # 戻り先
        try:
            return redirect(reverse("realized"))
        except Exception:
            try:
                return redirect(reverse("realized_trade_list"))
            except Exception:
                return redirect(reverse("stock_list"))

    ctx = {"init": {
        "ticker":       request.GET.get("ticker", ""),
        "stock_name":   request.GET.get("stock_name", ""),
        "account_type": request.GET.get("account_type", ""),
        "broker":       request.GET.get("broker", ""),
        "received_at":  request.GET.get("received_at", "") or str(date.today()),
    }}
    return render(request, "dividend_form.html", ctx)


@require_GET
def api_stock_lookup(request):
    ticker = (request.GET.get("ticker") or "").strip()
    if not ticker:
        return JsonResponse({"error": "ticker is required"}, status=400)

    qs = (Stock.objects
          .filter(ticker__iexact=ticker)
          .order_by(F("purchase_date").desc(nulls_last=True), "-id"))
    obj = qs.first()
    if not obj:
        return JsonResponse({"found": False}, status=404)

    data = {
        "found": True,
        "stock_name": obj.name,
        "account_type": obj.account_type,
        "broker": getattr(obj, "broker", ""),
        "shares": obj.shares,
    }
    return JsonResponse(data, status=200)


# =============================================================================
# 入出金（現金）
# =============================================================================
UNDO_WINDOW_SECONDS = 120

def _aggregate_balances() -> Dict[str, int]:
    sums = CashFlow.objects.values("broker", "flow_type").annotate(total=Sum("amount"))
    bal = {k: 0 for k, _ in BROKER_TABS}
    for row in sums:
        b = row["broker"]; t = row["flow_type"]; v = int(row["total"] or 0)
        if b in bal:
            bal[b] += v if t == "in" else -v
    return bal

@login_required
def cash_io_page(request):
    broker = request.GET.get("broker") or "rakuten"
    if broker not in BROKER_MAP:
        broker = "rakuten"
    active_label = BROKER_MAP[broker]

    range_days = (request.GET.get("range") or "").strip().lower()
    q = (request.GET.get("q") or "").strip()

    # POST: 登録
    if request.method == "POST":
        post_broker = request.POST.get("broker") or broker
        flow_type   = (request.POST.get("flow_type") or "").strip()
        amount_raw  = (request.POST.get("amount") or "").replace(",", "").strip()
        occurred_at = request.POST.get("occurred_at") or str(timezone.localdate())
        memo        = (request.POST.get("memo") or "").strip()

        amount = _safe_int(amount_raw, 0)
        occurred_date = _parse_date_yyyy_mm_dd(occurred_at)

        if post_broker not in BROKER_MAP:
            messages.error(request, "証券会社が不正です。")
        elif flow_type not in ("in", "out"):
            messages.error(request, "入金/出金を選んでください。")
        elif amount <= 0:
            messages.error(request, "金額を入力してください。")
        else:
            obj = CashFlow.objects.create(
                broker=post_broker, flow_type=flow_type, amount=amount,
                occurred_at=occurred_date, memo=memo[:200]
            )
            verb = "入金" if flow_type == "in" else "出金"
            messages.success(request, f"{BROKER_MAP[post_broker]} に {verb} {amount:,} 円を登録しました。")
            request.session["last_cashflow_id"] = obj.id
            request.session["last_cashflow_ts"] = timezone.now().timestamp()
            request.session.modified = True
            return redirect(f"{reverse('cash_io')}?broker={post_broker}&range={range_days or ''}&q={q}")

    # 残高
    balances = _aggregate_balances()

    # 履歴（このページでは「選択中の証券会社」のみを表示）
    qs = CashFlow.objects.filter(broker=broker)

    # 期間フィルタ
    if range_days and range_days.isdigit():
        since = timezone.localdate() - timedelta(days=int(range_days))
        qs = qs.filter(occurred_at__gte=since)

    # メモ検索
    if q:
        qs = qs.filter(Q(memo__icontains=q))

    recent = qs.order_by("-occurred_at", "-id")[:100]

    agg = qs.aggregate(
        in_sum=Sum(Case(When(flow_type="in", then=F("amount")), default=0, output_field=IntegerField())),
        out_sum=Sum(Case(When(flow_type="out", then=F("amount")), default=0, output_field=IntegerField())),
    )
    totals_in = int(agg["in_sum"] or 0)
    totals_out = int(agg["out_sum"] or 0)
    totals_net = totals_in - totals_out

    # Undo可否
    last_id = request.session.get("last_cashflow_id")
    last_ts = request.session.get("last_cashflow_ts")
    can_undo = bool(last_id and last_ts and (timezone.now().timestamp() - float(last_ts) <= UNDO_WINDOW_SECONDS))
    if last_id and last_ts and not can_undo:
        request.session.pop("last_cashflow_id", None)
        request.session.pop("last_cashflow_ts", None)
        request.session.modified = True

    ctx = {
        "tabs": BROKER_TABS,
        "active_broker": broker,
        "active_label": active_label,
        "balances": balances,
        "recent": recent,
        "today": str(timezone.localdate()),
        "can_undo": can_undo,
        "undo_id": last_id,
        "undo_seconds": UNDO_WINDOW_SECONDS,
        "totals_in": totals_in,
        "totals_out": totals_out,
        "totals_net": totals_net,
        "q": q,
        "range": range_days,
        "BROKER_MAP": BROKER_MAP,
    }
    return render(request, "cash_io.html", ctx)


@require_POST
def cash_undo(request):
    last_id = request.session.get("last_cashflow_id")
    last_ts = request.session.get("last_cashflow_ts")
    if not (last_id and last_ts):
        messages.error(request, "取り消せる入出金がありません。")
        return redirect(reverse("cash_io"))

    if timezone.now().timestamp() - float(last_ts) > UNDO_WINDOW_SECONDS:
        messages.error(request, "取り消し可能時間を過ぎました。")
        request.session.pop("last_cashflow_id", None)
        request.session.pop("last_cashflow_ts", None)
        request.session.modified = True
        return redirect(reverse("cash_io"))

    obj = get_object_or_404(CashFlow, pk=last_id)
    broker = obj.broker
    amt = obj.amount
    verb = "入金" if obj.flow_type == "in" else "出金"
    obj.delete()

    request.session.pop("last_cashflow_id", None)
    request.session.pop("last_cashflow_ts", None)
    request.session.modified = True

    messages.success(request, f"{BROKER_MAP.get(broker, broker)} の {verb} {amt:,} 円を取り消しました。")
    return redirect(f"{reverse('cash_io')}?broker={broker}")


@login_required
def cash_flow_edit_page(request, pk: int):
    obj = get_object_or_404(CashFlow, pk=pk)
    broker = obj.broker

    if request.method == "POST":
        amount_raw  = (request.POST.get("amount") or "").replace(",", "").strip()
        occurred_at = request.POST.get("occurred_at") or str(timezone.localdate())
        memo        = (request.POST.get("memo") or "").strip()

        amount = _safe_int(amount_raw, 0)
        occurred_date = _parse_date_yyyy_mm_dd(occurred_at)

        if amount <= 0:
            messages.error(request, "金額を入力してください。")
        else:
            obj.amount = amount
            obj.occurred_at = occurred_date
            obj.memo = memo[:200]
            obj.save(update_fields=["amount", "occurred_at", "memo", "updated_at"])
            messages.success(request, "入出金を更新しました。")
            return redirect(f"{reverse('cash_io')}?broker={broker}")

    ctx = {"item": obj, "broker_label": BROKER_MAP.get(broker, broker), "today": str(timezone.localdate())}
    return render(request, "cash_flow_edit.html", ctx)


@require_POST
def cash_flow_delete(request, pk: int):
    obj = get_object_or_404(CashFlow, pk=pk)
    broker = obj.broker
    amt = obj.amount
    verb = "入金" if obj.flow_type == "in" else "出金"
    obj.delete()
    messages.success(request, f"{BROKER_MAP.get(broker, broker)} の {verb} {amt:,} 円を削除しました。")
    return redirect(f"{reverse('cash_io')}?broker={broker}")


# 互換：古いコードが cash_view を参照してもOK
def cash_view(request, *args, **kwargs):
    return cash_io_page(request, *args, **kwargs)


# =============================================================================
# 設定画面
# =============================================================================
def settings_login(request):
    password_obj = SettingsPassword.objects.first()
    if not password_obj:
        return render(request, "settings_login.html", {"error": "パスワードが設定されていません。管理画面で作成してください。"})
    if request.method == "POST":
        password = request.POST.get("password") or ""
        if password == password_obj.password:
            request.session["settings_authenticated"] = True
            return redirect("settings")
        messages.error(request, "パスワードが違います")
    return render(request, "settings_login.html")


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


# =============================================================================
# 下部タブ API
# =============================================================================
@login_required
def get_tabs(request):
    tabs_qs = BottomTab.objects.prefetch_related("submenus").order_by("order", "id")
    data = []
    for tab in tabs_qs:
        data.append({
            "id": tab.id, "name": tab.name, "icon": tab.icon, "url_name": tab.url_name, "order": tab.order,
            "submenus": [{"id": sm.id, "name": sm.name, "url": sm.url, "order": sm.order} for sm in tab.submenus.all().order_by("order", "id")]
        })
    return JsonResponse(data, safe=False)


@csrf_exempt
@require_POST
@login_required
@models.transaction.atomic
def save_tab(request):
    import json
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
        tab.name = name; tab.icon = icon; tab.url_name = url_name; tab.save()
    else:
        max_tab = BottomTab.objects.order_by("-order").first()
        tab = BottomTab.objects.create(
            name=name, icon=icon, url_name=url_name, order=(max_tab.order + 1) if max_tab else 0
        )

    return JsonResponse({
        "id": tab.id, "name": tab.name, "icon": tab.icon, "url_name": tab.url_name, "order": tab.order,
        "submenus": [{"id": sm.id, "name": sm.name, "url": sm.url, "order": sm.order}
                     for sm in tab.submenus.all().order_by("order", "id")],
    })


@csrf_exempt
@require_POST
@login_required
def delete_tab(request, tab_id):
    tab = BottomTab.objects.filter(id=tab_id).first()
    if not tab:
        return JsonResponse({"error": "Tab not found"}, status=404)
    tab.delete()
    return JsonResponse({"success": True})


@csrf_exempt
@require_POST
@login_required
def save_order(request):
    import json
    try:
        data = json.loads(request.body.decode("utf-8"))
    except json.JSONDecodeError:
        return HttpResponseBadRequest("Invalid JSON")

    for idx, tab_id in enumerate(data):
        tab = BottomTab.objects.filter(id=tab_id).first()
        if tab:
            tab.order = idx
            tab.save()
    return JsonResponse({"success": True})


@csrf_exempt
@require_POST
@login_required
def save_submenu(request):
    import json
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
        sm.name = name; sm.url = url; sm.save()
    else:
        max_order = tab.submenus.aggregate(max_order=models.Max("order"))["max_order"] or 0
        sm = tab.submenus.create(name=name, url=url, order=max_order + 1)

    return JsonResponse({"id": sm.id, "name": sm.name, "url": sm.url, "order": sm.order})


@csrf_exempt
@require_POST
@login_required
def delete_submenu(request, sub_id):
    sm = SubMenu.objects.filter(id=sub_id).first()
    if not sm:
        return JsonResponse({"error": "Submenu not found"}, status=404)
    sm.delete()
    return JsonResponse({"success": True})


@csrf_exempt
@require_POST
@login_required
def save_submenu_order(request):
    import json
    try:
        data = json.loads(request.body.decode("utf-8"))
    except json.JSONDecodeError:
        return HttpResponseBadRequest("Invalid JSON")

    for idx, sub_id in enumerate(data):
        sm = SubMenu.objects.filter(id=sub_id).first()
        if sm:
            sm.order = idx
            sm.save()
    return JsonResponse({"success": True})


# =============================================================================
# マスタ検索API
# =============================================================================
def get_stock_by_code(request):
    code = (request.GET.get("code") or "").strip()
    stock = StockMaster.objects.filter(code=code).first()
    if stock:
        return JsonResponse(
            {"success": True, "name": stock.name, "sector": stock.sector},
            json_dumps_params={"ensure_ascii": False}
        )
    return JsonResponse({"success": False}, json_dumps_params={"ensure_ascii": False})


def suggest_stock_name(request):
    q = (request.GET.get("q") or "").strip()
    qs = StockMaster.objects.filter(name__icontains=q)[:10]
    data = [{"code": s.code, "name": s.name, "sector": s.sector or ""} for s in qs]
    return JsonResponse(data, safe=False, json_dumps_params={"ensure_ascii": False})


def get_sector_list(request):
    sectors = list(StockMaster.objects.values_list("sector", flat=True).distinct())
    return JsonResponse([s or "" for s in sectors], safe=False, json_dumps_params={"ensure_ascii": False})