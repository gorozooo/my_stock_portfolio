# portfolio/views/holding.py
from __future__ import annotations
import time
from dataclasses import dataclass
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import yfinance as yf
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import models
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, render, redirect
from django.views.decorators.http import require_POST
from statistics import median  # ★ 追加：中央値

from ..forms import HoldingForm
from ..models import Holding
from ..services import trend as svc_trend

# =========================================================
# ユーティリティ
# =========================================================

@dataclass
class RowVM:
    obj: Holding
    valuation: Optional[float] = None   # 現在評価額（合計）
    pnl: Optional[float] = None         # 含み損益（額）
    pnl_pct: Optional[float] = None     # 含み損益（%）
    days: Optional[int] = None          # 保有日数

    # 追加：テンプレが参照する値
    yield_now: Optional[float] = None   # 現在利回り（%）
    yield_cost: Optional[float] = None  # 取得利回り（%）
    div_annual: Optional[float] = None  # 年間配当（合計円）

    # スパーク
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
                if isinstance(df.columns, pd.MultiIndex):
                    # (TICKER, FIELD) の MultiIndex
                    if (nsym, "Close") in df.columns:
                        s = df[(nsym, "Close")]
                    else:
                        try:
                            s = df.xs(nsym, axis=1)["Close"]  # type: ignore[index]
                        except Exception:
                            return []
                else:
                    # 単一ティッカー
                    s = df["Close"]  # type: ignore[index]
            except Exception:
                return []
            try:
                vs = pd.Series(s).dropna().tail(ndays).values  # type: ignore[arg-type]
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
    - 年間配当（合計）/ 現在利回り / 取得利回り
    - スパーク：7/30/90日（指数/実値）
    """
    q = int(h.quantity or 0)
    avg_cost = _to_float(h.avg_cost or 0) or 0.0
    acq = q * avg_cost

    # 必要分をキャッシュ/バッチ取得
    n = _norm_ticker(h.ticker)
    raw7  = _preload_closes([h.ticker], 7).get(n, [])
    raw30 = _preload_closes([h.ticker], 30).get(n, [])
    raw90 = _preload_closes([h.ticker], 90).get(n, [])

    cur_price_ps: Optional[float] = None
    if raw7 or raw30 or raw90:
        cur_price_ps = float((raw30 or raw7 or raw90)[-1])

    valuation = float(cur_price_ps * q) if (cur_price_ps is not None and q) else None

    pnl = None
    pnl_pct = None
    if valuation is not None:
        pnl = valuation - acq
        if acq > 0:
            pnl_pct = (pnl / acq) * 100.0

    # ---- 年間配当・利回り ----
    # モデルの項目名が環境によって違っても拾えるようにゆるく解決
    def _get_div_ps(_h: Holding) -> Optional[float]:
        cand = [
            "div_ps", "dividend_ps", "dividend_per_share",
            "div_per_share", "dividend"  # per share で使っている場合向け
        ]
        for name in cand:
            if hasattr(_h, name):
                v = _to_float(getattr(_h, name))
                if v is not None:
                    return v
        return None

    def _get_div_total(_h: Holding) -> Optional[float]:
        # 既に合計（円）が入っているフィールドがある場合のフォールバック
        for name in ["div_annual", "dividend_annual", "annual_dividend"]:
            if hasattr(_h, name):
                v = _to_float(getattr(_h, name))
                if v is not None:
                    return v
        return None

    div_ps = _get_div_ps(h)                          # 1株あたり
    div_total = _get_div_total(h)                    # 合計（そのまま）※任意

    # per-share が取れたら合計を計算、無ければ既存の合計を使う
    if div_ps is not None:
        div_annual = div_ps * q
    else:
        div_annual = div_total

    # 利回り
    if div_ps is not None and cur_price_ps:          # 現在利回り
        yield_now = (div_ps / cur_price_ps) * 100.0
    else:
        yield_now = None

    if div_ps is not None and avg_cost > 0:          # 取得利回り
        yield_cost = (div_ps / avg_cost) * 100.0
    else:
        yield_cost = None

    # 保有日数
    start = h.opened_at or (h.created_at.date() if getattr(h, "created_at", None) else None)
    days = (_today_jst() - start).days if start else None

    # 指数化（価格の指数化）
    s7_idx  = _indexize(raw7)
    s30_idx = _indexize(raw30)
    s90_idx = _indexize(raw90)

    return RowVM(
        obj=h,
        valuation=valuation,
        pnl=pnl,
        pnl_pct=pnl_pct,
        days=days,
        yield_now=yield_now,
        yield_cost=yield_cost,
        div_annual=div_annual,
        s7_idx=s7_idx or None,
        s30_idx=s30_idx or None,
        s90_idx=s90_idx or None,
        s7_raw=raw7 or None,
        s30_raw=raw30 or None,
        s90_raw=raw90 or None,
    )

# ---------- 集計（ページ内KPIを“濃く”） ----------
def _aggregate(rows: List[RowVM]) -> Dict[str, Optional[float]]:
    """
    ブローカー/口座などのフィルタ後の rows だけを対象に、
    画面上部のKPIをまとめて返す。
    """
    n = 0
    acq_sum = 0.0
    val_sum = 0.0
    have_val = 0
    winners = losers = 0
    days_list: List[int] = []
    top_gain: Optional[Tuple[float, Holding]] = None
    top_loss: Optional[Tuple[float, Holding]] = None

    for r in rows:
        h = r.obj
        n += 1
        q = int(h.quantity or 0)
        cost = _to_float(h.avg_cost or 0) or 0.0
        acq_i = q * cost
        acq_sum += acq_i

        if r.valuation is not None:
            val_sum += float(r.valuation)
            have_val += 1

        if r.pnl is not None:
            if r.pnl > 0:
                winners += 1
            elif r.pnl < 0:
                losers += 1
            # トップ益/損
            if top_gain is None or r.pnl > top_gain[0]:
                top_gain = (r.pnl, h)
            if top_loss is None or r.pnl < top_loss[0]:
                top_loss = (r.pnl, h)

        if r.days is not None:
            days_list.append(int(r.days))

    pnl_sum: Optional[float] = (val_sum - acq_sum) if have_val else None
    pnl_pct: Optional[float] = (pnl_sum / acq_sum * 100.0) if (pnl_sum is not None and acq_sum > 0) else None
    win_rate: Optional[float] = (winners / (winners + losers) * 100.0) if (winners + losers) > 0 else None

    avg_days: Optional[float] = (sum(days_list) / len(days_list)) if days_list else None
    med_days: Optional[float] = (median(days_list) if days_list else None)
    avg_pos_size: Optional[float] = (acq_sum / n) if n else None

    summary: Dict[str, Optional[float]] = dict(
        count=n,
        acq=acq_sum,
        val=val_sum if have_val else None,
        pnl=pnl_sum,
        pnl_pct=pnl_pct,
        winners=winners,
        losers=losers,
        win_rate=win_rate,
        avg_days=avg_days,
        med_days=med_days,
        avg_pos_size=avg_pos_size,
    )
    # 表示用の補助情報（テンプレで存在チェックして使う）
    if top_gain:
        summary["top_gain_pnl"] = top_gain[0]
        summary["top_gain_id"] = top_gain[1].id
    if top_loss:
        summary["top_loss_pnl"] = top_loss[0]
        summary["top_loss_id"] = top_loss[1].id
    return summary

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
    q = (request.GET.get("q") or request.GET.get("ticker") or "").strip()
    if q:
        qs = qs.filter(
            models.Q(ticker__icontains=q) | models.Q(name__icontains=q)
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
        # pnl/days は DB で並べ替えできないため、ここでは更新日の降順にしておく
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

def _sort_rows(rows: List[RowVM], request) -> List[RowVM]:
    """
    pnl / days ソートを行う（“ページ内”での見た目ソート）。
    """
    sort = (request.GET.get("sort") or "").lower()
    order = (request.GET.get("order") or "desc").lower()
    reverse = (order != "asc")

    if sort == "pnl":
        rows.sort(key=lambda r: (r.pnl is None, r.pnl or 0.0), reverse=reverse)
    elif sort == "days":
        rows.sort(key=lambda r: (r.days is None, r.days or 0), reverse=reverse)
    # それ以外（updated/created/opened）は DB ソート済み
    return rows

@login_required
def holding_list(request):
    qs = Holding.objects.filter(user=request.user)
    qs = _apply_filters(qs, request)
    qs = _sort_qs(qs, request)
    page = _page(request, qs)

    rows = _build_rows_for_page(page)
    rows = _apply_post_filters(rows, request)
    rows = _sort_rows(rows, request)

    # KPI集計（ブレイクダウンは廃止）
    summary = _aggregate(rows)

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
        "summary": summary,
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
    rows = _sort_rows(rows, request)

    summary = _aggregate(rows)

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
        "summary": summary,
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