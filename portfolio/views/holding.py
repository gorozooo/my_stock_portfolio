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
    # スパーク：指数化（index）と実値（raw）の両方を 7/30/90 日分
    s7_idx: Optional[List[float]] = None
    s30_idx: Optional[List[float]] = None
    s90_idx: Optional[List[float]] = None
    s7_raw: Optional[List[float]] = None
    s30_raw: Optional[List[float]] = None
    s90_raw: Optional[List[float]] = None

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

# ------- yfinance 価格バッチ取得（15分キャッシュ） -------
# key = (ticker_norm, days) -> (ts, [closes...])
_SPARK_CACHE: Dict[Tuple[str, int], Tuple[float, List[float]]] = {}

def _cache_get(ticker_norm: str, days: int) -> Optional[List[float]]:
    item = _SPARK_CACHE.get((ticker_norm, days))
    if not item:
        return None
    ts, arr = item
    # 15分キャッシュ
    if time.time() - ts < 15 * 60:
        return arr
    return None

def _cache_put(ticker_norm: str, days: int, closes: List[float]) -> None:
    _SPARK_CACHE[(ticker_norm, days)] = (time.time(), closes)

def _preload_closes(tickers: List[str], days: int) -> Dict[str, List[float]]:
    """
    複数のティッカーをまとめて days 日分の終値に解決する。
    可能な限りキャッシュを使い、未キャッシュ分だけ yfinance を呼ぶ。
    戻り値は {ticker_norm: [closes...]}。
    """
    need: List[str] = []
    out: Dict[str, List[float]] = {}
    ndays = max(days, 1)

    for t in tickers:
        n = _norm_ticker(t)
        cached = _cache_get(n, ndays)
        if cached is not None:
            out[n] = cached
        else:
            need.append(n)

    if need:
        # 市場休場などを考慮し少し広めに取得
        period_days = max(ndays + 10, 40 if ndays <= 30 else 110)
        try:
            df = yf.download(
                tickers=need if len(need) > 1 else need[0],
                period=f"{period_days}d",
                interval="1d",
                auto_adjust=True,
                progress=False,
                group_by="ticker",
            )
        except Exception:
            df = None

        def _pick_one(nsym: str) -> List[float]:
            if df is None:
                return []
            try:
                # 単銘柄/複数銘柄の両ケースを吸収
                if isinstance(df.columns, yf.pandas.MultiIndex):  # type: ignore[attr-defined]
                    s = df[(nsym, "Close")]
                else:
                    # need が一つの時
                    s = df["Close"]
                s = svc_trend._pick_field(df if nsym in need and isinstance(df.columns, yf.pandas.MultiIndex) else s, "Close", required=False)  # type: ignore
            except Exception:
                try:
                    s = df[nsym]["Close"]  # type: ignore[index]
                except Exception:
                    return []
            try:
                vs = s.dropna().tail(ndays).values
                return [float(v) for v in list(vs)]
            except Exception:
                return []

        for n in need:
            closes = _pick_one(n)
            _cache_put(n, ndays, closes)
            out[n] = closes

    return out

def _indexize(arr: List[float]) -> List[float]:
    if not arr:
        return []
    base = arr[0]
    if base == 0:
        return arr[:]
    return [round(v / base, 4) for v in arr]

def _build_row(h: Holding) -> RowVM:
    """
    1件分の表示用データを作る。
    - 現在評価額 / 含み損益 / % / 保有日数
    - スパーク：7/30/90日（指数/実値）
    """
    q = int(h.quantity or 0)
    acq = (q * _to_float(h.avg_cost or 0)) or 0.0

    # 必要分をキャッシュ/バッチ取得
    n = _norm_ticker(h.ticker)
    raw7  = _preload_closes([h.ticker], 7).get(n, [])
    raw30 = _preload_closes([h.ticker], 30).get(n, [])
    raw90 = _preload_closes([h.ticker], 90).get(n, [])

    val_now = None
    if raw7 or raw30 or raw90:
        last = (raw30 or raw7 or raw90)[-1]
        val_now = last * q

    pnl = None
    pnl_pct = None
    if val_now is not None:
        pnl = val_now - acq
        if acq > 0:
            pnl_pct = (pnl / acq) * 100.0

    # 保有日数
    start = h.opened_at or h.created_at.date()
    days = (_today_jst() - start).days if start else None

    # 指数化（評価額の指数にするより、価格の指数の方が直感的）
    s7_idx  = _indexize(raw7)
    s30_idx = _indexize(raw30)
    s90_idx = _indexize(raw90)

    return RowVM(
        obj=h,
        valuation=val_now,
        pnl=pnl,
        pnl_pct=pnl_pct,
        days=days,
        s7_idx=s7_idx or None,
        s30_idx=s30_idx or None,
        s90_idx=s90_idx or None,
        s7_raw=raw7 or None,
        s30_raw=raw30 or None,
        s90_raw=raw90 or None,
    )

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
# 一覧（フィルタ/並び替え/ページング）
# =========================================================
def _apply_filters(qs, request):
    """
    クエリの値が「choicesのコード」でも「表示名」でもヒットするように正規化してから絞り込み。
    例）broker= 'MATSUI' でも '松井証券' でも可。
    """
    def _normalize_choice(field_name: str, raw: str) -> Optional[str]:
        """
        Holding.<field_name>.choices から raw をコードに変換する。
        - raw がコード or 表示名のどちらでも受け付ける
        - 大文字小文字/全角半角/前後空白をゆるく吸収
        - 'ALL' / 'すべて' / '' は None を返してフィルタ無しに
        """
        if raw is None:
            return None
        s = str(raw).strip()
        if s == "" or s.upper() == "ALL" or s == "すべて":
            return None

        # 正規化（大文字/全角半角）
        import unicodedata
        key = unicodedata.normalize("NFKC", s).strip()

        field = Holding._meta.get_field(field_name)
        for value, label in (field.choices or []):
            v = str(value)
            l = unicodedata.normalize("NFKC", str(label)).strip()
            # コード一致 or 表示名一致のどちらでもOK
            if key == v or key == l:
                return value
        # choices に無い値はそのまま返す（既にコードを渡しているケース等）
        return s

    # 証券（broker）、口座（account）、売買（side）
    broker  = _normalize_choice("broker",  request.GET.get("broker"))
    account = _normalize_choice("account", request.GET.get("account"))
    side    = _normalize_choice("side",    request.GET.get("side"))

    if broker:
        qs = qs.filter(broker=broker)
    if account:
        qs = qs.filter(account=account)
    if side:
        qs = qs.filter(side=side)

    # テキスト検索：コード/名称にゆるくヒット
    # UI が 'q' でも 'ticker' でも来ても拾う
    q = (request.GET.get("q") or request.GET.get("ticker") or "").strip()
    if q:
        qs = qs.filter(
            # icontains の OR
            (models.Q(ticker__icontains=q) | models.Q(name__icontains=q))
        )

    return qs

def _sort_qs(qs, request):
    sort = request.GET.get("sort") or "updated"  # updated|created|opened
    order = request.GET.get("order") or "desc"   # asc|desc
    if sort in ("updated", "created", "opened"):
        field = {"updated":"updated_at","created":"created_at","opened":"opened_at"}[sort]
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

def _build_rows_for_page(page):
    return [_build_row(h) for h in page.object_list]

def _apply_post_filters(rows: List[RowVM], request) -> List[RowVM]:
    """
    価格計算後でしかフィルタできない条件（損益プラス/マイナスなど）を
    “ページ内” に適用。※ページング越えの厳密さは必要なら別途実装
    """
    pnl_sign = (request.GET.get("pnl") or "").upper()  # POS|NEG|""(all)
    if pnl_sign == "POS":
        rows = [r for r in rows if (r.pnl or 0) > 0]
    elif pnl_sign == "NEG":
        rows = [r for r in rows if (r.pnl or 0) < 0]
    return rows

@login_required
def holding_list(request):
    qs = Holding.objects.filter(user=request.user)
    qs = _apply_filters(qs, request)
    qs = _sort_qs(qs, request)
    page = _page(request, qs)

    rows = _build_rows_for_page(page)
    rows = _apply_post_filters(rows, request)

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
        "sort": request.GET.get("sort") or "updated",
        "order": request.GET.get("order") or "desc",
        "filters": {
            "broker": request.GET.get("broker") or "",
            "account": request.GET.get("account") or "",
            "ticker": request.GET.get("ticker") or "",
            "side":   request.GET.get("side") or "",
            "pnl":    request.GET.get("pnl") or "",
        },
    }
    return render(request, "holdings/list.html", ctx)

@login_required
def holding_list_partial(request):
    qs = Holding.objects.filter(user=request.user)
    qs = _apply_filters(qs, request)
    qs = _sort_qs(qs, request)
    page = _page(request, qs)

    rows = _build_rows_for_page(page)
    rows = _apply_post_filters(rows, request)

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
        "sort": request.GET.get("sort") or "updated",
        "order": request.GET.get("order") or "desc",
        "filters": {
            "broker": request.GET.get("broker") or "",
            "account": request.GET.get("account") or "",
            "ticker": request.GET.get("ticker") or "",
            "side":   request.GET.get("side") or "",
            "pnl":    request.GET.get("pnl") or "",
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