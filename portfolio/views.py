from __future__ import annotations

# ==== 標準 / 外部 ====
import json
import logging
import re
import datetime as dt
from datetime import datetime, timedelta
from typing import Any, Dict, List, Tuple, TypedDict, Optional

import yfinance as yf

# ==== Django ====
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.core.exceptions import ImproperlyConfigured
from django.db import transaction, models
from django.db.models import F, Value, Case, When, CharField, Sum, Q, IntegerField
from django.http import (
    JsonResponse,
    HttpResponseBadRequest,
    HttpResponse,
    Http404,
)
from django.shortcuts import render, redirect, get_object_or_404
from django.template.loader import get_template, render_to_string
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import cache_page
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST, require_GET, require_http_methods

# ==== 自作 ====
from .forms import SettingsPasswordForm
from .utils import get_bottom_tabs
from .models import (
    BottomTab,
    SubMenu,
    Stock,
    StockMaster,
    SettingsPassword,
    RealizedProfit,
)

# Optional: 存在すれば使う
try:
    from .models import Dividend
except Exception:
    Dividend = None  # type: ignore

# Optional: ニュースの外部モデル
try:
    from .models import StockNews  # type: ignore
    HAS_STOCK_NEWS_MODEL = True
except Exception:
    HAS_STOCK_NEWS_MODEL = False

# Optional: 外部ニュースプロバイダ
FETCH_PROVIDER = None
try:
    from app.services.news_provider import fetch_stock_news as _fetch_from_provider  # type: ignore
    FETCH_PROVIDER = _fetch_from_provider
except Exception:
    FETCH_PROVIDER = None

logger = logging.getLogger(__name__)

# -----------------------------
# 共通コンテキスト
# -----------------------------
def bottom_tabs_context(request):
    return {"BOTTOM_TABS": get_bottom_tabs()}

# -----------------------------
# メイン画面（※関数本体は変更なし）
# -----------------------------
from collections import defaultdict
from datetime import timedelta
from django.db.models import Sum
from django.utils import timezone
from django.contrib.auth.decorators import login_required
from django.shortcuts import render

# ───────── フォールバック（無くても落ちないように） ─────────
try:
    BROKER_TABS  # noqa: F401
except NameError:
    BROKER_TABS = [("rakuten", "楽天証券"), ("matsui", "松井証券"), ("sbi", "SBI証券")]
    BROKER_MAP = dict(BROKER_TABS)

try:
    from .models import Stock
except Exception:
    Stock = None  # type: ignore

try:
    from .models import RealizedProfit
except Exception:
    RealizedProfit = None  # type: ignore

try:
    from .models import Dividend
except Exception:
    Dividend = None  # type: ignore

try:
    from .models import CashFlow
except Exception:
    CashFlow = None  # type: ignore


# ──────────────────────── ユーティリティ ─────────────────────────────
def _safe_int(x, default=0) -> int:
    try:
        return int(x)
    except Exception:
        try:
            return int(float(x))
        except Exception:
            return default


def _safe_float(x, default=0.0) -> float:
    try:
        f = float(x)
        return f if f == f else default  # NaN→default
    except Exception:
        return default


def _model_has_user_field(model) -> bool:
    try:
        return "user" in {f.name for f in model._meta.get_fields()}
    except Exception:
        return False


def _norm_broker_key(value: str) -> str:
    if not value:
        return ""
    s = str(value).strip().lower()
    if "rakuten" in s or "楽天" in s:
        return "rakuten"
    if "matsui" in s or "松井" in s:
        return "matsui"
    if "sbi" in s:
        return "sbi"
    return ""


def _val_first(obj, names: list[str], default=None):
    """objから候補フィールド名を順に見て最初にある値を返す"""
    for n in names:
        if hasattr(obj, n):
            v = getattr(obj, n)
            if v is not None and v != "":
                return v
    return default


def _bool_first(obj, names: list[str]) -> bool | None:
    v = _val_first(obj, names, None)
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    if s in ("1", "true", "yes", "y", "t"):
        return True
    if s in ("0", "false", "no", "n", "f"):
        return False
    return None


def _stock_queryset_for_user(request):
    if not Stock:
        return []
    qs = Stock.objects.all()
    if _model_has_user_field(Stock):
        qs = qs.filter(user=request.user)
    return qs


# ───────── 手数料・税の取り込み（フィールド名ぶれ吸収） ─────────
def _extract_fees(obj) -> float:
    names = [
        "fee", "fees", "commission", "commissions",
        "tax", "taxes", "stamp_tax", "other_fee", "broker_fee",
    ]
    total = 0.0
    for n in names:
        if hasattr(obj, n):
            total += _safe_float(getattr(obj, n))
    return max(total, 0.0)


# ───────── 現物/信用・買い/売り 判定（多系統フィールド対応） ─────────
def _detect_side(obj) -> tuple[str, bool]:
    """
    return: (acct_group, is_short)
      acct_group: "spot" or "margin"
      is_short  : True(売建) / False(買 or 現物)
    """
    # 1) 明示フラグ優先
    is_margin = _bool_first(obj, ["is_margin", "margin_flag", "is_credit", "credit_flag"])
    is_short  = _bool_first(obj, ["is_short", "short_flag"])
    side_str  = (_val_first(obj, ["side", "position", "pos"], "") or "").lower()

    # 2) 文字列で side 判定
    if is_short is None:
        if any(k in side_str for k in ("short", "sell", "売", "売り", "売建")):
            is_short = True
        elif any(k in side_str for k in ("long", "buy", "買", "買い", "買建")):
            is_short = False

    # 3) 口座種別・信用種別で margin/spot 判定
    acct_text = " ".join(
        str(_val_first(obj, names, "") or "")
        for names in [
            ["account_type", "account", "account_category", "category", "kind"],
            ["margin_type", "credit_type"],
        ]
    ).upper()

    # NISA/特定/一般/つみたて → 基本 spot
    if any(k in acct_text for k in ("NISA", "特定", "一般", "つみたて", "一般ＮＩＳＡ", "新ＮＩＳＡ", "成長", "つみたてＮＩＳＡ")):
        acct_group = "spot"
    # 信用ワード
    elif any(k in acct_text for k in ("信", "MARGIN", "CREDIT", "制度", "一般信用")):
        acct_group = "margin"
    else:
        # 明示フラグがあれば従う
        if is_margin is True:
            acct_group = "margin"
        elif is_margin is False:
            acct_group = "spot"
        else:
            # side 未判定なら現物に倒す
            acct_group = "spot"

    # 売建は常に信用扱い
    if is_short is True:
        acct_group = "margin"

    # 最終補正：side 不明は False に倒す（現物/信用買）
    return acct_group, bool(is_short)


# ───────── 価格・コストの取得（候補フィールド横断） ─────────
def _pick_current_price(obj) -> float:
    return _safe_float(_val_first(obj, [
        "current_price", "last_price", "price", "close_price", "market_price", "last",
    ], 0.0))


def _pick_unit_price(obj) -> float:
    return _safe_float(_val_first(obj, [
        "unit_price", "avg_price", "average_price", "average_cost", "book_unit_price", "cost_price",
    ], 0.0))


def _pick_quantity(obj) -> float:
    q = _safe_float(_val_first(obj, [
        "shares", "quantity", "qty", "units", "lot",
    ], 0.0))
    return abs(q)  # 評価規模は常に正値


def _pick_cost(obj, unit: float, qty: float, is_short: bool) -> float:
    """
    優先順: total_cost → book_cost → book_value → average_cost/avg_price*qty → unit*qty
    手数料は total_cost が無いときのみ加減算
    """
    # 優先: total_cost/book_cost/book_value
    v = _val_first(obj, ["total_cost", "book_cost", "book_value"], None)
    if v is not None:
        return _safe_float(v)

    avg = _safe_float(_val_first(obj, ["average_cost", "avg_cost", "avg_price", "average_price"], 0.0))
    base = avg if avg > 0 else unit
    gross = base * qty

    fees = _extract_fees(obj)
    if is_short:
        return gross - fees   # 売付受渡額（手数料控除）
    return gross + fees       # 取得原価（手数料加算）


# ───────── 1銘柄メトリクス（現物/信用・ロング/ショート対応） ─────────
def _compute_position_metrics(stock_obj) -> dict:
    acct_group, is_short = _detect_side(stock_obj)
    qty = _pick_quantity(stock_obj)
    unit = _pick_unit_price(stock_obj)
    curr = _pick_current_price(stock_obj) or unit
    cost = _pick_cost(stock_obj, unit, qty, is_short)

    market_value = curr * qty  # 規模（ショートも正値）
    if is_short:
        unrealized = cost - curr * qty          # (売付受渡額) − (現価買戻し額)
    else:
        unrealized = curr * qty - cost          # (評価額) − (取得原価)

    return {
        "group": acct_group,        # "spot" or "margin"
        "is_short": is_short,       # True/False
        "shares": qty,
        "unit_price": unit,
        "current_price": curr,
        "cost": cost,
        "market_value": market_value,
        "unrealized_pl": unrealized,
    }


def _top_positions(stocks, topn=5):
    rows = []
    for s in stocks:
        m = _compute_position_metrics(s)
        rows.append({
            "ticker": getattr(s, "ticker", "") or "",
            "name": getattr(s, "name", "") or "",
            "shares": int(round(m["shares"])),
            "market_value": m["market_value"],
        })
    rows.sort(key=lambda r: _safe_float(r["market_value"]), reverse=True)
    return rows[:topn]


# ───────── 最近の動き（各社・全体） ─────────
def _recent_for_broker(broker_key: str, request, limit=6):
    items = []

    if RealizedProfit:
        q = RealizedProfit.objects.all()
        if _model_has_user_field(RealizedProfit):
            q = q.filter(user=request.user)
        q = q.order_by("-date", "-id")[:300]
        for t in q:
            if _norm_broker_key(getattr(t, "broker", "")) != broker_key:
                continue
            pnl = _safe_float(getattr(t, "profit_amount", 0.0))
            items.append({
                "kind": "trade",
                "kind_label": "売買",
                "date": getattr(t, "date", timezone.localdate()),
                "ticker": getattr(t, "code", "") or "",
                "name": getattr(t, "stock_name", "") or "",
                "pnl": pnl,
                "sign": "+" if pnl >= 0 else "-",
                "memo": "",
            })

    if Dividend:
        qd = Dividend.objects.all()
        if _model_has_user_field(Dividend):
            qd = qd.filter(user=request.user)
        qd = qd.order_by("-received_at", "-id")[:300]
        for d in qd:
            if _norm_broker_key(getattr(d, "broker", "")) != broker_key:
                continue
            net = getattr(d, "net_amount", None)
            net = _safe_int(net) if net is not None else (_safe_int(getattr(d, "gross_amount", 0)) - _safe_int(getattr(d, "tax", 0)))
            items.append({
                "kind": "dividend",
                "kind_label": "配当",
                "date": getattr(d, "received_at", timezone.localdate()),
                "ticker": getattr(d, "ticker", "") or "",
                "name": getattr(d, "stock_name", "") or "",
                "net": net,
                "sign": "+" if net >= 0 else "-",
                "memo": getattr(d, "memo", "") or "",
            })

    if CashFlow:
        qc = CashFlow.objects.all().order_by("-occurred_at", "-id")[:300]
        if _model_has_user_field(CashFlow):
            qc = qc.filter(user=request.user)
        for c in qc:
            bkey = _norm_broker_key(getattr(c, "broker", ""))
            if bkey != broker_key:
                continue
            amt = _safe_int(getattr(c, "amount", 0))
            flow = getattr(c, "flow_type", "")
            items.append({
                "kind": "cash",
                "kind_label": "現金",
                "date": getattr(c, "occurred_at", timezone.localdate()),
                "broker_label": BROKER_MAP.get(bkey, bkey),
                "amount": amt,
                "flow": flow,
                "sign": "+" if flow == "in" else "-",
                "memo": getattr(c, "memo", "") or "",
            })

    items.sort(key=lambda r: (r.get("date") or timezone.localdate(), r.get("kind") or ""), reverse=True)
    return items[:limit]


def _recent_all(request, days: int | None):
    horizon = timezone.localdate() - timedelta(days=days) if isinstance(days, int) else None
    rows = []

    if RealizedProfit:
        q = RealizedProfit.objects.all()
        if _model_has_user_field(RealizedProfit):
            q = q.filter(user=request.user)
        if horizon:
            q = q.filter(date__gte=horizon)
        q = q.order_by("-date", "-id")[:300]
        for t in q:
            pnl = _safe_float(getattr(t, "profit_amount", 0.0))
            rows.append({
                "kind": "trade",
                "kind_label": "売買",
                "date": getattr(t, "date", timezone.localdate()),
                "ticker": getattr(t, "code", "") or "",
                "name": getattr(t, "stock_name", "") or "",
                "pnl": pnl,
                "memo": "",
            })

    if Dividend:
        qd = Dividend.objects.all()
        if _model_has_user_field(Dividend):
            qd = qd.filter(user=request.user)
        if horizon:
            qd = qd.filter(received_at__gte=horizon)
        qd = qd.order_by("-received_at", "-id")[:300]
        for d in qd:
            net = getattr(d, "net_amount", None)
            net = _safe_int(net) if net is not None else (_safe_int(getattr(d, "gross_amount", 0)) - _safe_int(getattr(d, "tax", 0)))
            rows.append({
                "kind": "dividend",
                "kind_label": "配当",
                "date": getattr(d, "received_at", timezone.localdate()),
                "ticker": getattr(d, "ticker", "") or "",
                "name": getattr(d, "stock_name", "") or "",
                "net": net,
                "memo": getattr(d, "memo", "") or "",
            })

    if CashFlow:
        qc = CashFlow.objects.all()
        if _model_has_user_field(CashFlow):
            qc = qc.filter(user=request.user)
        if horizon:
            qc = qc.filter(occurred_at__gte=horizon)
        qc = qc.order_by("-occurred_at", "-id")[:300]
        for c in qc:
            rows.append({
                "kind": "cash",
                "kind_label": "現金",
                "date": getattr(c, "occurred_at", timezone.localdate()),
                "broker_label": BROKER_MAP.get(_norm_broker_key(getattr(c, "broker", "")), getattr(c, "broker", "")),
                "amount": _safe_int(getattr(c, "amount", 0)),
                "flow": getattr(c, "flow_type", ""),
                "memo": getattr(c, "memo", "") or "",
            })

    rows.sort(key=lambda r: (r.get("date") or timezone.localdate(), r.get("kind") or ""), reverse=True)
    return rows[:100]


# ───────────────────────── メインビュー ────────────────────────────
@login_required
def main_page(request):
    """
    - 現物（現物＋NISA）と信用（買建／売建）を確実に分離
    - P/L はロング/ショートで式を分ける（手数料も考慮）
    - 価格/数量/コストは候補フィールドを総当たりで取得
    - ブローカー名は正規化
    """
    broker_tabs = BROKER_TABS
    active_broker = request.GET.get("broker") or "rakuten"
    if active_broker not in dict(broker_tabs):
        active_broker = "rakuten"

    # ===== 保有集計 =====
    spot_mv = margin_mv = 0.0
    spot_upl = margin_upl = 0.0

    holdings_count_by_broker = defaultdict(int)
    market_value_by_broker = defaultdict(float)
    upl_by_broker = defaultdict(float)
    stocks_by_broker = defaultdict(list)

    qs = _stock_queryset_for_user(request)
    for s in qs:
        m = _compute_position_metrics(s)
        if m["group"] == "spot":
            spot_mv += m["market_value"]
            spot_upl += m["unrealized_pl"]
        else:
            margin_mv += m["market_value"]
            margin_upl += m["unrealized_pl"]

        braw = getattr(s, "broker", "")
        if hasattr(braw, "name"):
            braw = getattr(braw, "name", "")
        bkey = _norm_broker_key(str(braw))
        if bkey:
            holdings_count_by_broker[bkey] += 1
            market_value_by_broker[bkey] += m["market_value"]
            upl_by_broker[bkey] += m["unrealized_pl"]
            stocks_by_broker[bkey].append(s)

    # ===== 現金残高 =====
    broker_balances = {"rakuten": 0, "matsui": 0, "sbi": 0}
    cash_total = 0
    if CashFlow:
        cf = CashFlow.objects.all()
        if _model_has_user_field(CashFlow):
            cf = cf.filter(user=request.user)
        sums = cf.values("broker", "flow_type").annotate(total=Sum("amount"))
        for row in sums:
            bkey = _norm_broker_key(row.get("broker") or "")
            if not bkey:
                continue
            amt = _safe_int(row.get("total", 0))
            if (row.get("flow_type") or "") != "in":
                amt = -amt
            broker_balances[bkey] += amt
        cash_total = sum(broker_balances.values())

    # ===== 総資産・前日比 =====
    total_assets = spot_mv + margin_mv + cash_total
    day_change = 0
    target_assets = 0

    # ===== ブローカー統計・上位ポジション =====
    def _bk_stats(k):
        return {
            "holdings_count": holdings_count_by_broker.get(k, 0),
            "market_value": market_value_by_broker.get(k, 0.0),
            "unrealized_pl": upl_by_broker.get(k, 0.0),
        }

    broker_stats = {
        "rakuten": _bk_stats("rakuten"),
        "matsui": _bk_stats("matsui"),
        "sbi": _bk_stats("sbi"),
    }
    top_positions_by_broker = {
        "rakuten": _top_positions(stocks_by_broker.get("rakuten", []), 5),
        "matsui": _top_positions(stocks_by_broker.get("matsui", []), 5),
        "sbi": _top_positions(stocks_by_broker.get("sbi", []), 5),
    }

    # ===== 最近の動き =====
    recent_by_broker = {
        "rakuten": _recent_for_broker("rakuten", request),
        "matsui": _recent_for_broker("matsui", request),
        "sbi": _recent_for_broker("sbi", request),
    }

    # ===== 実現損益（配当加味） =====
    realized_pl_mtd = realized_pl_ytd = realized_pl_total = 0
    today = timezone.localdate()
    first_m = today.replace(day=1)
    first_y = today.replace(month=1, day=1)

    if RealizedProfit:
        base = RealizedProfit.objects.all()
        if _model_has_user_field(RealizedProfit):
            base = base.filter(user=request.user)
        agg_total = base.aggregate(s=Sum("profit_amount"))
        agg_mtd = base.filter(date__gte=first_m).aggregate(s=Sum("profit_amount"))
        agg_ytd = base.filter(date__gte=first_y).aggregate(s=Sum("profit_amount"))
        realized_pl_total += _safe_int(agg_total.get("s", 0))
        realized_pl_mtd   += _safe_int(agg_mtd.get("s", 0))
        realized_pl_ytd   += _safe_int(agg_ytd.get("s", 0))

    if Dividend:
        dq = Dividend.objects.all()
        if _model_has_user_field(Dividend):
            dq = dq.filter(user=request.user)

        def _div_sum(qs):
            s = 0
            for d in qs:
                net = getattr(d, "net_amount", None)
                net = _safe_int(net) if net is not None else (_safe_int(getattr(d, "gross_amount", 0)) - _safe_int(getattr(d, "tax", 0)))
                s += net
            return s

        realized_pl_total += _div_sum(dq)
        realized_pl_mtd   += _div_sum(dq.filter(received_at__gte=first_m))
        realized_pl_ytd   += _div_sum(dq.filter(received_at__gte=first_y))

    # ===== スパークライン（無ければフラット） =====
    try:
        asset_history_csv = ",".join([str(int(round(total_assets)))] * 30)
    except Exception:
        asset_history_csv = ""

    # ===== 最近アクティビティ（全体） =====
    rng = (request.GET.get("range") or "7").lower()
    days = {"7": 7, "30": 30, "90": 90}.get(rng)
    recent_activities = _recent_all(request, days)

    # ===== コンテキスト =====
    ctx = dict(
        total_assets=total_assets,
        day_change=day_change,
        target_assets=target_assets,

        # ← テンプレで使えるよう分離して渡す
        spot_market_value=spot_mv,        # 現物＋NISA 評価額
        margin_market_value=margin_mv,    # 信用 評価額
        spot_unrealized_pl=spot_upl,      # 現物＋NISA 含み損益
        margin_unrealized_pl=margin_upl,  # 信用 含み損益
        unrealized_pl_total=spot_upl + margin_upl,

        # 互換（既存の main.html が参照していても動くように）
        portfolio_value=spot_mv + margin_mv,
        unrealized_pl=spot_upl + margin_upl,

        asset_history_csv=asset_history_csv,

        broker_tabs=broker_tabs,
        active_broker=active_broker,
        broker_balances=broker_balances,
        broker_stats=broker_stats,
        top_positions_by_broker=top_positions_by_broker,
        recent_by_broker=recent_by_broker,

        realized_pl_mtd=realized_pl_mtd,
        realized_pl_ytd=realized_pl_ytd,
        realized_pl_total=realized_pl_total,

        recent_activities=recent_activities,
    )
    return render(request, "main.html", ctx)
    
# -----------------------------
# 認証（関数本体は変更なし）
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


# ---------------------------------------
# 株一覧（関数本体は変更なし）
# ---------------------------------------
PRICE_CACHE_TTL = 15 * 60  # 15分

@login_required
def stock_list_view(request):
    qs = Stock.objects.all()
    try:
        field_names = {f.name for f in Stock._meta.get_fields()}
        if "user" in field_names:
            qs = qs.filter(user=request.user)
    except Exception as e:
        logger.debug("User filter not applied: %s", e)

    # broker_name 正規化
    try:
        broker_field = Stock._meta.get_field("broker")
        broker_type = broker_field.get_internal_type()
        if broker_type == "CharField" and getattr(Stock, "BROKER_CHOICES", None):
            whens = [When(broker=code, then=Value(label)) for code, label in Stock.BROKER_CHOICES]
            broker_name_annot = Case(*whens, default=F("broker"), output_field=CharField())
        elif broker_type == "ForeignKey":
            qs = qs.select_related("broker")
            broker_name_annot = F("broker__name")
        else:
            broker_name_annot = F("broker")
    except Exception as e:
        logger.warning("broker_name annotate fallback: %s", e)
        broker_name_annot = Value("（未設定）", output_field=CharField())

    # account_type_name 正規化
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

    qs = qs.annotate(
        broker_name=broker_name_annot,
        account_type_name=account_name_annot,
    ).order_by("broker_name", "account_type_name", "name", "ticker")

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
    if not ticker:
        return float(fallback or 0.0)

    cache_key = f"price:{ticker}"
    cached = cache.get(cache_key)
    if isinstance(cached, (int, float)):
        return float(cached)

    symbol = f"{ticker}.T"
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


# -----------------------------
# 以降の関数は“既存そのまま”。（追記のみ / インポート解決済み）
# -----------------------------

@login_required
def stock_create(request):
    errors = {}
    data = {}

    if request.method == "POST":
        data = request.POST

        # --- 購入日 ---
        purchase_date = None
        purchase_date_str = (data.get("purchase_date") or "").strip()
        if purchase_date_str:
            try:
                purchase_date = datetime.date.fromisoformat(purchase_date_str)  # type: ignore[attr-defined]
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

        # --- ポジション ---
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

        try:
            total_cost = float(data.get("total_cost")) if data.get("total_cost") not in (None, "",) else (shares * unit_price)
        except (TypeError, ValueError):
            total_cost = shares * unit_price

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

            try:
                if "user" in {f.name for f in Stock._meta.get_fields()}:
                    create_kwargs["user"] = request.user
            except Exception:
                pass

            Stock.objects.create(**create_kwargs)
            return redirect("stock_list")

    else:
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
    stock = get_object_or_404(Stock, pk=pk)
    errors = []

    current_price_for_view = float(stock.current_price or 0.0)
    if current_price_for_view <= 0:
        try:
            symbol = f"{stock.ticker}.T" if not str(stock.ticker).endswith(".T") else stock.ticker
            todays = yf.Ticker(symbol).history(period="1d")
            if not todays.empty:
                current_price_for_view = float(todays["Close"].iloc[-1])
        except Exception:
            current_price_for_view = 0.0

    def extract_securities_code(ticker_or_code: str) -> str:
        if not ticker_or_code:
            return ""
        s = str(ticker_or_code).strip()
        s = re.sub(r'\.[A-Za-z]+$', '', s)
        m = re.match(r'^(\d{4})', s)
        return m.group(1) if m else ""

    if request.method == "POST":
        mode = (request.POST.get("sell_mode") or "").strip()

        try:
            shares_to_sell = int(request.POST.get("shares") or 0)
        except (TypeError, ValueError):
            shares_to_sell = 0

        try:
            limit_price = float(request.POST.get("limit_price") or 0)
        except (TypeError, ValueError):
            limit_price = 0.0

        sell_date_str = (request.POST.get("sell_date") or "").strip()
        sold_at = timezone.now()
        if sell_date_str:
            try:
                sell_date = datetime.date.fromisoformat(sell_date_str)  # type: ignore[attr-defined]
                sold_at_naive = datetime.combine(sell_date, dt.time(15, 0, 0))
                sold_at = timezone.make_aware(sold_at_naive, timezone.get_current_timezone())
            except Exception:
                errors.append("売却日が不正です。YYYY-MM-DD 形式で指定してください。")

        try:
            actual_profit_input = request.POST.get("actual_profit", "")
            actual_profit = float(actual_profit_input) if actual_profit_input != "" else 0.0
        except (TypeError, ValueError):
            actual_profit = 0.0
            errors.append("実際の損益額は数値で入力してください。")

        if mode not in ("market", "limit"):
            errors.append("売却方法が不正です。")

        if shares_to_sell <= 0:
            errors.append("売却株数を 1 以上で指定してください。")
        elif shares_to_sell > int(stock.shares or 0):
            errors.append("保有株数を超える売却はできません。")

        price = None
        if mode == "market":
            price = float(stock.current_price or current_price_for_view or stock.unit_price or 0)
        else:
            if limit_price <= 0:
                errors.append("指値価格を正しく入力してください。")
            else:
                price = limit_price

        if not price or price <= 0:
            errors.append("売却価格が不正です。")

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

        unit_price = float(stock.unit_price or 0)
        estimated_amount = float(price) * shares_to_sell
        total_profit_est = (float(price) - unit_price) * shares_to_sell
        fee = estimated_amount - float(actual_profit or 0.0)

        final_profit_amount = actual_profit if actual_profit != 0.0 else total_profit_est

        profit_rate_val = None
        denom = unit_price * shares_to_sell
        if denom:
            profit_rate_val = round((final_profit_amount / denom) * 100, 2)

        posted_code = (request.POST.get("code") or request.POST.get("ticker") or "").strip()
        stock_code    = getattr(stock, "code", "") or ""
        ticker_code   = extract_securities_code(getattr(stock, "ticker", "") or "")
        final_code    = posted_code or stock_code or ticker_code or ""

        posted_broker = (request.POST.get("broker") or "").strip()
        posted_acct   = (request.POST.get("account_type") or "").strip()
        final_broker  = posted_broker or getattr(stock, "broker", "") or ""
        final_account = posted_acct   or getattr(stock, "account_type", "") or ""

        RealizedProfit.objects.create(
            user=request.user,
            date=sold_at.date(),
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
            profit_rate=profit_rate_val,
        )

        remaining = int(stock.shares or 0) - shares_to_sell
        if remaining <= 0:
            stock.delete()
        else:
            stock.shares = remaining
            stock.total_cost = int(round(remaining * unit_price))
            stock.save(update_fields=["shares", "total_cost", "updated_at"])

        return redirect("stock_list")

    return render(
        request,
        "stocks/sell_stock_page.html",
        {
            "stock": stock,
            "errors": errors,
            "current_price": current_price_for_view or 0.0,
        },
    )


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
    return render(request, "stocks/edit_page.html", {"stock": stock})


def edit_stock_fragment(request, pk):
    stock = get_object_or_404(Stock, pk=pk)
    return render(request, "stocks/edit_form.html", {"stock": stock})


@login_required
@require_GET
def stock_detail_fragment(request, pk: int):
    stock = get_object_or_404(Stock, pk=pk)
    html = render_to_string("stocks/_detail_modal.html", {"stock": stock}, request=request)
    return HttpResponse(html)


@login_required
@require_GET
@cache_page(60 * 1440)
def stock_price_json(request, pk: int):
    stock = get_object_or_404(Stock, pk=pk)
    ticker = Stock.to_yf_symbol(stock.ticker) if hasattr(Stock, "to_yf_symbol") else stock.ticker

    period_q = (request.GET.get("period") or "1M").upper()
    if period_q not in ("1M", "3M", "1Y"):
        period_q = "1M"

    today = timezone.localdate()

    if period_q == "1M":
        start_range = today - dt.timedelta(days=60)
        cap_points = 30
    elif period_q == "3M":
        start_range = today - dt.timedelta(days=150)
        cap_points = 60
    else:
        start_range = today - dt.timedelta(days=430)
        cap_points = 260

    start_52w = today - dt.timedelta(days=400)

    series: List[Dict[str, float]] = []
    last_close = None
    prev_close = None
    high_52w = None
    low_52w = None
    high_all = None
    low_all = None

    try:
        tkr = yf.Ticker(ticker)
        hist = tkr.history(
            start=start_range.isoformat(),
            end=(today + dt.timedelta(days=1)).isoformat(),
            interval="1d",
        )
        if not hist.empty:
            df = hist[["Open", "High", "Low", "Close"]].dropna()
            if not df.empty:
                tail = df.tail(cap_points)
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

        hist_all = tkr.history(period="max", interval="1d")
        if not hist_all.empty:
            hh_all = hist_all["High"].dropna()
            ll_all = hist_all["Low"].dropna()
            if not hh_all.empty:
                high_all = float(hh_all.max())
            if not ll_all.empty:
                low_all = float(ll_all.min())

    except Exception:
        pass

    if not last_close or last_close <= 0:
        last_close = float(stock.current_price or stock.unit_price or 0.0)
    if not prev_close or prev_close <= 0:
        prev_close = last_close

    change = last_close - prev_close
    change_pct = (change / prev_close * 100.0) if prev_close else 0.0

    if not series:
        series = []

    data = {
        "period": period_q,
        "series": series,
        "last_close": last_close,
        "prev_close": prev_close,
        "change": change,
        "change_pct": change_pct,
        "high_52w": high_52w,
        "low_52w": low_52w,
        "high_all": high_all,
        "low_all": low_all,
    }
    return JsonResponse(data)


# --- Fundamental JSON（既存のまま） ---
@cache_page(60 * 1440)
@login_required
@require_GET
def stock_fundamental_json(request, pk: int):
    stock = get_object_or_404(Stock, pk=pk)
    symbol = Stock.to_yf_symbol(stock.ticker) if hasattr(Stock, "to_yf_symbol") else stock.ticker

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
    div_yield_pct = None
    dps = None
    updated = timezone.now().isoformat()

    try:
        tkr = yf.Ticker(symbol)
        fi = getattr(tkr, "fast_info", {}) or {}

        def fi_get(key):
            try:
                if isinstance(fi, dict):
                    return fi.get(key, None)
                return getattr(fi, key, None)
            except Exception:
                return None

        lp = fi_get("last_price")
        if lp:
            last_price = float(lp)

        dy = fi_get("dividend_yield")
        if dy is not None:
            try:
                dyf = float(dy)
                div_yield_pct = dyf * 100.0 if dyf < 1 else dyf
            except Exception:
                pass

        trpe = fi_get("trailing_pe")
        if trpe:
            try:
                per = float(trpe)
            except Exception:
                pass

        fmc = fi_get("market_cap")
        if fmc:
            try:
                mcap = float(fmc)
            except Exception:
                pass

        info = {}
        try:
            info = tkr.info or {}
        except Exception:
            info = {}

        if per is None:
            v = info.get("trailingPE")
            if v:
                try:
                    per = float(v)
                except Exception:
                    pass

        if pbr is None:
            v = info.get("priceToBook")
            if v:
                try:
                    pbr = float(v)
                except Exception:
                    pass

        if eps is None:
            v = info.get("trailingEps")
            if v:
                try:
                    eps = float(v)
                except Exception:
                    pass

        if mcap is None:
            v = info.get("marketCap")
            if v:
                try:
                    mcap = float(v)
                except Exception:
                    pass

        v = info.get("dividendRate")
        if v:
            try:
                dps = float(v)
            except Exception:
                pass

        if (div_yield_pct is None or dps is None):
            try:
                divs = tkr.dividends
                if divs is not None and not divs.empty:
                    since = timezone.now().date() - dt.timedelta(days=400)
                    ttm = divs[divs.index.date >= since]
                    if not ttm.empty:
                        ttm_sum = float(ttm.sum())
                        if dps is None:
                            dps = ttm_sum
                        if div_yield_pct is None and last_price:
                            div_yield_pct = (ttm_sum / float(last_price)) * 100.0
            except Exception:
                pass

        if dps is None and div_yield_pct is not None and last_price:
            dps = (float(div_yield_pct) / 100.0) * float(last_price)

    except Exception:
        pass

    if not last_price:
        try:
            last_price = float(stock.current_price or stock.unit_price or 0.0)
        except Exception:
            last_price = 0.0

    def clean_num(x):
        try:
            f = float(x)
            if f != f:
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
        "dividend_yield_pct": clean_num(div_yield_pct),
        "dividend_per_share": clean_num(dps),
        "updated_at": updated,
    }
    return JsonResponse(data)

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
        "current_price": current_price,  # ← カード値で上書きされ得る
        "total_cost": total_cost,
        "market_value": market_value,
        "profit_loss": profit_loss,
        "note": stock.note or "",
        "updated_at": stock.updated_at.isoformat() if stock.updated_at else None,
    }
    return JsonResponse(data)


# --- ニュース JSON（既存のまま / 型補助） ---
class NewsItemDict(TypedDict, total=False):
    id: str
    title: str
    url: str
    source: str
    published_at: str
    summary: str
    sentiment: str
    impact: int
    date: str


def _to_utc_isoz(dt_obj: dt.datetime) -> str:
    if timezone.is_naive(dt_obj):
        dt_obj = timezone.make_aware(dt_obj, timezone.get_current_timezone())
    dt_utc = dt_obj.astimezone(timezone.utc)
    return dt_utc.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _as_date_string(dt_obj: dt.datetime, *, tz: Optional[dt.tzinfo] = None) -> str:
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
    if url.startswith("http://") or url.startswith("https://"):
        return url
    return ""


def _serialize_from_dict(d: Dict[str, Any]) -> NewsItemDict:
    title = str(d.get("title") or "").strip()
    source = str(d.get("source") or "").strip()
    pub = d.get("published_at")
    if isinstance(pub, dt.datetime):
        published_at = _to_utc_isoz(pub)
        date_str = _as_date_string(pub)
    else:
        try:
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
    if not HAS_STOCK_NEWS_MODEL:
        return ([], 0, False)

    qs = StockNews.objects.filter(stock=stock).order_by("-published_at")
    total = qs.count()
    start = (page - 1) * limit
    end = start + limit
    objs = list(qs[start:end])
    items = [_serialize_from_model(m) for m in objs]
    has_more = end < total
    return (items, total, has_more)


@login_required
@require_GET
@cache_page(300)
def stock_news_json(request, pk: int):
    stock = get_object_or_404(Stock, pk=pk)

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

    MIN_LIMIT = 10
    MAX_LIMIT = 50
    DEFAULT_LIMIT = 20

    page = _parse_positive_int(request.GET.get("page", "1"), default=1, min_v=1, max_v=10_000)
    limit = _parse_positive_int(request.GET.get("limit", str(DEFAULT_LIMIT)), DEFAULT_LIMIT, MIN_LIMIT, MAX_LIMIT)
    lang = (request.GET.get("lang") or "all").lower()

    items: List[NewsItemDict] = []
    total: Optional[int] = None
    has_more: bool = False

    if FETCH_PROVIDER:
        try:
            raw_items, provider_total = FETCH_PROVIDER(stock=stock, page=page, limit=limit, lang=lang)
            items = [_serialize_from_dict(d) for d in (raw_items or [])]
            total = provider_total if isinstance(provider_total, int) and provider_total >= 0 else None
            if total is not None:
                has_more = page * limit < total
            else:
                has_more = len(items) >= limit
        except Exception:
            items, total_db, has_more = _fetch_from_db(stock, page, limit, lang)
            total = total_db if total_db > 0 else None
    else:
        items, total_db, has_more = _fetch_from_db(stock, page, limit, lang)
        total = total_db if total_db > 0 else None

    payload: Dict[str, Any] = {
        "page": page,
        "limit": limit,
        "has_more": has_more,
        "items": items,
    }
    if total is not None:
        payload["total"] = total

    return JsonResponse(payload, json_dumps_params={"ensure_ascii": False})


# -----------------------------
# 実現損益（既存のまま）
# -----------------------------
def _model_has_field(model, name: str) -> bool:
    try:
        model._meta.get_field(name)
        return True
    except Exception:
        return False


@login_required
def realized_view(request):
    trades_qs = RealizedProfit.objects.filter(user=request.user).order_by("-date", "-id")

    trade_rows = []
    for t in trades_qs:
        trade_rows.append({
            "date":        t.date,
            "stock_name":  t.stock_name,
            "code":        getattr(t, "code", None),
            "broker":      t.broker,
            "account_type":t.account_type,
            "trade_type":  "sell",
            "quantity":    getattr(t, "quantity", None),
            "profit_amount":getattr(t, "profit_amount", 0),
            "profit_rate": getattr(t, "profit_rate", None),
            "purchase_price":getattr(t, "purchase_price", None),
            "sell_price":  getattr(t, "sell_price", None),
            "fee":         getattr(t, "fee", None),
            "id":          t.id,
            "_kind":       "trade",
        })

    div_qs = Dividend.objects.all() if Dividend else []
    if Dividend and _model_has_field(Dividend, "user"):
        div_qs = div_qs.filter(user=request.user)  # type: ignore[attr-defined]

    if Dividend:
        div_qs = div_qs.order_by("-received_at", "-id")  # type: ignore[assignment]

    div_rows = []
    if Dividend:
        for d in div_qs:  # type: ignore[operator]
            div_rows.append({
                "date":        d.received_at,
                "stock_name":  d.stock_name,
                "code":        getattr(d, "ticker", None),
                "broker":      d.broker,
                "account_type":d.account_type,
                "trade_type":  "dividend",
                "quantity":    None,
                "profit_amount":getattr(d, "net_amount", 0),
                "profit_rate": None,
                "purchase_price": None,
                "sell_price":  None,
                "fee":         None,
                "id":          d.id,
                "_kind":       "dividend",
            })

    merged = trade_rows + div_rows
    merged.sort(key=lambda r: (r["date"], r["id"]), reverse=True)

    from collections import OrderedDict as _OD
    groups = _OD()
    for row in merged:
        y = row["date"].year
        m = row["date"].month
        ym = f"{y:04d}-{m:02d}"
        groups.setdefault(ym, []).append(row)

    totals = {
        "count": len(merged),
        "sum_profit": sum((r["profit_amount"] or 0) for r in merged),
        "sum_profit_only": sum((r["profit_amount"] or 0) for r in merged if (r["profit_amount"] or 0) > 0),
        "sum_loss_only": sum((r["profit_amount"] or 0) for r in merged if (r["profit_amount"] or 0) < 0),
    }

    return render(request, "realized.html", {
        "rows_by_ym": groups,
        "totals": totals,
    })


@login_required
def trade_history(request):
    return render(request, "trade_history.html")


# -----------------------------
# 配当入力（既存のまま）
# -----------------------------
from datetime import date as _date_alias  # 既存関数のための補助

def dividend_new_page(request):
    if request.method == "POST":
        ticker       = (request.POST.get("ticker") or "").strip()
        stock_name   = (request.POST.get("stock_name") or "").strip()
        received_at  = request.POST.get("received_at") or str(_date_alias.today())
        gross_amount = int(request.POST.get("gross_amount") or 0)
        tax          = int(request.POST.get("tax") or 0)
        account_type = (request.POST.get("account_type") or "").strip()
        broker       = (request.POST.get("broker") or "").strip()
        memo         = (request.POST.get("memo") or "").strip()

        if not ticker or not stock_name or gross_amount <= 0:
            messages.error(request, "必須項目（銘柄名・コード・配当金）を入力してください。")
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

        if Dividend:
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

        try:
            return redirect(reverse("realized"))
        except Exception:
            try:
                return redirect(reverse("realized_trade_list"))
            except Exception:
                return redirect(reverse("stock_list"))

    ctx = {
        "init": {
            "ticker":       request.GET.get("ticker", ""),
            "stock_name":   request.GET.get("stock_name", ""),
            "account_type": request.GET.get("account_type", ""),
            "broker":       request.GET.get("broker", ""),
            "received_at":  request.GET.get("received_at", "") or str(_date_alias.today()),
        }
    }
    return render(request, "dividend_form.html", ctx)


# -----------------------------
# 配当入力 補助API（既存のまま）
# -----------------------------
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


# -----------------------------
# 登録ハブ（既存のまま）
# -----------------------------
@login_required
def register_hub(request):
    settings_cards = [
        {
            "url_name": "stock_create",
            "color": "#3AA6FF",
            "icon": "fa-circle-plus",
            "title": "新規登録",
            "description": "保有株を素早く追加",
            "badge": "推奨",
            "progress": None,
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
            "url_name": "cash_io",
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
# 入出金（既存のまま / import 整理済み）
# -----------------------------
BROKER_TABS = [
    ("rakuten", "楽天証券"),
    ("matsui",  "松井証券"),
    ("sbi",     "SBI証券"),
]
BROKER_MAP = dict(BROKER_TABS)
UNDO_WINDOW_SECONDS = 120

from .models import CashFlow  # ここで一度だけ

def _aggregate_balances():
    sums = (CashFlow.objects.values("broker", "flow_type").annotate(total=Sum("amount")))
    bal = {k: 0 for k, _ in BROKER_TABS}
    for row in sums:
        b = row["broker"]; t = row["flow_type"]; v = row["total"] or 0
        if b in bal:
            bal[b] += v if t == "in" else -v
    return bal

def _parse_date_yyyy_mm_dd(s: str):
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except Exception:
        return timezone.now().date()

def cash_io_page(request):
    broker = request.GET.get("broker") or "rakuten"
    if broker not in BROKER_MAP:
        broker = "rakuten"
    active_label = BROKER_MAP[broker]

    range_days = (request.GET.get("range") or "").strip().lower()
    q = (request.GET.get("q") or "").strip()

    if request.method == "POST":
        post_broker = request.POST.get("broker") or broker
        flow_type   = (request.POST.get("flow_type") or "").strip()
        amount_raw  = (request.POST.get("amount") or "").replace(",", "").strip()
        occurred_at = request.POST.get("occurred_at") or str(timezone.now().date())
        memo        = (request.POST.get("memo") or "").strip()

        try:
            amount = int(amount_raw)
        except ValueError:
            amount = 0
        occurred_date = _parse_date_yyyy_mm_dd(occurred_at)

        if post_broker not in BROKER_MAP:
            messages.error(request, "証券会社が不正です。")
        elif flow_type not in ("in", "out"):
            messages.error(request, "入金/出金を選んでください。")
        elif amount <= 0:
            messages.error(request, "金額を入力してください。")
        else:
            obj = CashFlow.objects.create(
                broker=post_broker,
                flow_type=flow_type,
                amount=amount,
                occurred_at=occurred_date,
                memo=memo[:200],
            )
            verb = "入金" if flow_type == "in" else "出金"
            messages.success(request, f"{BROKER_MAP[post_broker]} に {verb} {amount:,} 円を登録しました。")
            request.session["last_cashflow_id"] = obj.id
            request.session["last_cashflow_ts"] = timezone.now().timestamp()
            request.session.modified = True
            return redirect(f"{reverse('cash_io')}?broker={post_broker}&range={range_days or ''}&q={q}")

    balances = _aggregate_balances()
    qs = CashFlow.objects.filter(broker=broker)

    if range_days and range_days.isdigit():
        since = timezone.now().date() - timedelta(days=int(range_days))
        qs = qs.filter(occurred_at__gte=since)

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
        "today": str(timezone.now().date()),
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


def cash_flow_edit_page(request, pk: int):
    obj = get_object_or_404(CashFlow, pk=pk)
    broker = obj.broker

    if request.method == "POST":
        amount_raw  = (request.POST.get("amount") or "").replace(",", "").strip()
        occurred_at = request.POST.get("occurred_at") or str(timezone.now().date())
        memo        = (request.POST.get("memo") or "").strip()

        try:
            amount = int(amount_raw)
        except ValueError:
            amount = 0
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

    ctx = {
        "item": obj,
        "broker_label": BROKER_MAP.get(broker, broker),
        "today": str(timezone.now().date()),
    }
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


def cash_view(request, *args, **kwargs):
    return cash_io_page(request, *args, **kwargs)


# -----------------------------
# 設定（既存のまま）
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


# -----------------------------
# タブ API（既存のまま）
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


# -----------------------------
# マスタ検索系（既存のまま）
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


def suggest_stock_name(request):
    q = (request.GET.get("q") or "").strip()
    qs = StockMaster.objects.filter(name__icontains=q)[:10]
    data = [
        {"code": s.code, "name": s.name, "sector": s.sector or ""}
        for s in qs
    ]
    return JsonResponse(data, safe=False, json_dumps_params={"ensure_ascii": False})


def get_sector_list(request):
    sectors = list(StockMaster.objects.values_list("sector", flat=True).distinct())
    return JsonResponse([s or "" for s in sectors], safe=False, json_dumps_params={"ensure_ascii": False})