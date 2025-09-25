# portfolio/views/holding.py
from __future__ import annotations
import time
from dataclasses import dataclass
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

import yfinance as yf
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, render, redirect
from django.views.decorators.http import require_POST

from ..forms import HoldingForm
from ..models import Holding
from ..services import trend as svc_trend

# =========================================================
# ユーティリティ
# =========================================================

@dataclass
class RowVM:
    obj: Holding
    valuation: Optional[float] = None   # 現在評価額
    pnl: Optional[float] = None         # 含み損益（額）
    pnl_pct: Optional[float] = None     # 含み損益（%）
    days: Optional[int] = None          # 保有日数
    spark: Optional[List[float]] = None # スパークライン用配列（30本まで）

def _to_float(x) -> Optional[float]:
    try:
        if x is None:
            return None
        return float(x)
    except Exception:
        return None

def _norm_ticker(raw: str) -> str:
    """'8591' / '167A' → yfinance用（例: '8591.T' / '167A.T'）"""
    return svc_trend._normalize_ticker(str(raw or ""))

def _today_jst() -> date:
    return date.today()

# ------- yfinance ベースの簡易価格取得 -------
_SPARK_CACHE: Dict[Tuple[str, int], Tuple[float, List[float]]] = {}  # (ts, close_list)

def _recent_closes(ticker: str, days: int = 30) -> List[float]:
    """
    直近days営業日の終値を返す（最大30）/ メモリキャッシュ1時間
    """
    key = (_norm_ticker(ticker), days)
    now = time.time()
    cached = _SPARK_CACHE.get(key)
    if cached and now - cached[0] < 3600:  # 1h
        return cached[1]

    yf_t = key[0]
    # バッファを少し広めに（市場休場対策）
    period_days = max(days + 10, 40)
    df = yf.download(
        yf_t,
        period=f"{period_days}d",
        interval="1d",
        auto_adjust=True,
        progress=False,
    )
    if df is None or df.empty:
        closes: List[float] = []
    else:
        s = svc_trend._pick_field(df, "Close", required=True).dropna().tail(days)
        closes = [float(v) for v in list(s.values)]

    _SPARK_CACHE[key] = (now, closes)
    return closes

def _valuation_now(ticker: str, quantity: int) -> Optional[float]:
    closes = _recent_closes(ticker, days=1)
    if not closes:
        return None
    try:
        return float(closes[-1]) * int(quantity or 0)
    except Exception:
        return None

def _build_row(h: Holding, *, want_spark: bool = True) -> RowVM:
    """
    1件分の表示用データを作る。
    - 現在評価額 / 含み損益 / % / 保有日数
    - spark: 直近30日の評価額推移を「始値=1.0」基準指数化して返す
    """
    q = int(h.quantity or 0)
    acq = (q * _to_float(h.avg_cost or 0)) or 0.0

    closes = _recent_closes(h.ticker, days=30)
    val_now = None
    if closes:
        val_now = closes[-1] * q
    elif q:
        # 価格が取れない時は評価額＝None（表示は—）
        val_now = None

    pnl = None
    pnl_pct = None
    if val_now is not None:
        pnl = val_now - acq
        if acq > 0:
            pnl_pct = (pnl / acq) * 100.0

    # 保有日数
    start = h.opened_at or h.created_at.date()
    days = (_today_jst() - start).days if start else None

    # spark（直近30日の評価額を index 化：最初の値を1.0）
    spark: Optional[List[float]] = None
    if want_spark and closes and q > 0:
        vals = [c * q for c in closes]
        base = vals[0] if vals else 0.0
        if base and base > 0:
            spark = [round(v / base, 3) for v in vals]  # 例: 0.98, 1.01, 1.05 ...
        else:
            # 万一base=0ならそのまま（前段のSVGがmin/maxで正規化）
            spark = [round(v, 2) for v in vals]

    return RowVM(obj=h, valuation=val_now, pnl=pnl, pnl_pct=pnl_pct, days=days, spark=spark)

# =========================================================
# API: コード→銘柄名（既存）
# =========================================================
@login_required
def api_ticker_name(request):
    raw = (request.GET.get("code") or request.GET.get("q") or "").strip()
    norm = svc_trend._normalize_ticker(raw)
    code = (norm.split(".", 1)[0] if norm else raw).upper()

    override = getattr(settings, "TSE_NAME_OVERRIDES", {}).get(code)
    if override:
        return JsonResponse({"code": code, "name": override})

    name = svc_trend._lookup_name_jp_from_list(norm) or ""
    if not name:
        try:
            name = svc_trend._fetch_name_prefer_jp(norm) or ""
        except Exception:
            name = ""
    return JsonResponse({"code": code, "name": name})

# =========================================================
# 一覧（フィルタ/並び替え/ページングは既存のGETクエリ仕様を踏襲）
# =========================================================
def _apply_filters(qs, request):
    broker = request.GET.get("broker") or ""
    account = request.GET.get("account") or ""
    ticker = (request.GET.get("ticker") or "").strip()
    if broker and broker != "ALL":
        qs = qs.filter(broker=broker)
    if account and account != "ALL":
        qs = qs.filter(account=account)
    if ticker:
        qs = qs.filter(ticker__icontains=ticker)
    return qs

def _sort_qs(qs, request):
    sort = request.GET.get("sort") or "updated"  # updated|pnl|days
    order = request.GET.get("order") or "desc"   # asc|desc
    # ここではDBソートは更新日/作成日程度にし、
    # pnl/daysはページング後にPythonで整列する（価格が必要なため）
    if sort in ("updated", "created", "opened"):
        field = {
            "updated": "updated_at",
            "created": "created_at",
            "opened":  "opened_at",
        }[sort]
        if order == "asc":
            qs = qs.order_by(field, "-id")
        else:
            qs = qs.order_by(f"-{field}", "-id")
    else:
        qs = qs.order_by("-updated_at", "-id")
    return qs

def _page(request, qs, per_page: int = 10):
    p = int(request.GET.get("page") or 1)
    paginator = Paginator(qs, per_page)
    return paginator.get_page(p)

@login_required
def holding_list(request):
    qs = Holding.objects.filter(user=request.user)
    qs = _apply_filters(qs, request)
    qs = _sort_qs(qs, request)
    page = _page(request, qs)

    # ページに出る分だけSpark等を計算（軽量化）
    rows: List[RowVM] = [_build_row(h, want_spark=True) for h in page.object_list]

    # Python側ソート（損益/日数など、価格に依存する項目）
    sort = request.GET.get("sort") or "updated"
    order = request.GET.get("order") or "desc"
    if sort in ("pnl", "days"):
        key = (lambda r: (r.pnl if sort == "pnl" else (r.days or 0)))
        rows.sort(key=key, reverse=(order != "asc"))

    # 置き換えた配列をpage風の薄いオブジェクトに詰め直してテンプレへ
    class _PageWrap:
        def __init__(self, src, objs):
            self.number = src.number
            self.paginator = src.paginator
            self.has_previous = src.has_previous
            self.has_next = src.has_next
            self.previous_page_number = src.previous_page_number
            self.next_page_number = src.next_page_number
            self.object_list = objs
    page_wrap = _PageWrap(page, rows)

    ctx = {
        "page": page_wrap,
        "sort": sort,
        "order": order,
        "filters": {
            "broker": request.GET.get("broker") or "",
            "account": request.GET.get("account") or "",
            "ticker": request.GET.get("ticker") or "",
        },
    }
    return render(request, "holdings/list.html", ctx)

# HTMX用：一覧だけ差し替え（既存テンプレ `_list.html` を返す）
@login_required
def holding_list_partial(request):
    qs = Holding.objects.filter(user=request.user)
    qs = _apply_filters(qs, request)
    qs = _sort_qs(qs, request)
    page = _page(request, qs)

    rows: List[RowVM] = [_build_row(h, want_spark=True) for h in page.object_list]

    sort = request.GET.get("sort") or "updated"
    order = request.GET.get("order") or "desc"
    if sort in ("pnl", "days"):
        key = (lambda r: (r.pnl if sort == "pnl" else (r.days or 0)))
        rows.sort(key=key, reverse=(order != "asc"))

    class _PageWrap:
        def __init__(self, src, objs):
            self.number = src.number
            self.paginator = src.paginator
            self.has_previous = src.has_previous
            self.has_next = src.has_next
            self.previous_page_number = src.previous_page_number
            self.next_page_number = src.next_page_number
            self.object_list = objs
    page_wrap = _PageWrap(page, rows)

    ctx = {
        "page": page_wrap,
        "sort": sort,
        "order": order,
        "filters": {
            "broker": request.GET.get("broker") or "",
            "account": request.GET.get("account") or "",
            "ticker": request.GET.get("ticker") or "",
        },
    }
    return render(request, "holdings/_list.html", ctx)

# =========================================================
# 作成/編集/削除（既存）
# =========================================================
@login_required
def holding_create(request):
    if request.method == "POST":
        form = HoldingForm(request.POST)
        if form.is_valid():
            obj = form.save(commit=False)
            obj.user = request.user
            obj.save()
            messages.success(request, "保有を登録しました。")
            return redirect("holding_list")
    else:
        form = HoldingForm()
    return render(request, "holdings/form.html", {"form": form, "mode": "create"})

@login_required
def holding_edit(request, pk):
    obj = get_object_or_404(Holding, pk=pk, user=request.user)
    if request.method == "POST":
        form = HoldingForm(request.POST, instance=obj)
        if form.is_valid():
            form.save()
            messages.success(request, "保有を更新しました。")
            return redirect("holding_list")
    else:
        form = HoldingForm(instance=obj)
    return render(request, "holdings/form.html", {"form": form, "mode": "edit", "obj": obj})

@login_required
@require_POST
def holding_delete(request, pk: int):
    filters = {"pk": pk}
    if any(f.name == "user" for f in Holding._meta.fields):
        filters["user"] = request.user
    h = get_object_or_404(Holding, **filters)
    h.delete()
    if request.headers.get("HX-Request") == "true":
        return HttpResponse("")
    return redirect("holding_list")