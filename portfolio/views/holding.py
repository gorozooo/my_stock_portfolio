# -*- coding: utf-8 -*-
from __future__ import annotations
from datetime import date, timedelta
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple, Union
from decimal import Decimal
from dataclasses import dataclass
import random
import time

import pandas as pd
import yfinance as yf
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import models
from django.db.models import Sum
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from statistics import median  # ★ 追加：中央値

from ..forms import HoldingForm
from ..models import Holding
from ..services import trend as svc_trend

Number = Union[int, float, Decimal]

# =========================================================
# ユーティリティ
# =========================================================

SECTOR_CACHE_TTL = 30 * 60  # 30分
_SECTOR_CACHE: Dict[str, Tuple[float, str]] = {}  # code(.T含む正規化) -> (ts, sector_text)


def _sector_cache_get(norm: str) -> Optional[str]:
    item = _SECTOR_CACHE.get(norm)
    if not item:
        return None
    ts, sec = item
    if time.time() - ts < SECTOR_CACHE_TTL:
        return sec
    return None


def _sector_cache_put(norm: str, sector: str) -> None:
    if sector:
        _SECTOR_CACHE[norm] = (time.time(), sector)


def _to_float(x) -> Optional[float]:
    try:
        if x is None:
            return None
        return float(x)
    except Exception:
        return None


def _get_fx_usd_jpy(ttl: int = 15 * 60) -> Optional[float]:
    """
    USD/JPY 為替レート（1USD あたり何円か）を yfinance から取得。
    簡易キャッシュ付き（デフォルト 15分）。
    """
    now = time.time()
    cache = getattr(_get_fx_usd_jpy, "_cache", None)
    if cache:
        ts, rate = cache
        if now - ts < ttl and rate is not None:
            return rate  # キャッシュ有効

    try:
        df = yf.download(
            "JPY=X",
            period="5d",
            interval="1d",
            auto_adjust=False,
            progress=False,
        )
        if df is None or df.empty:
            return cache[1] if cache else None  # 前回値があればそれを返す

        close = df["Close"].dropna()
        if close.empty:
            return cache[1] if cache else None

        rate = float(close.iloc[-1])  # 1USD = rate JPY
        _get_fx_usd_jpy._cache = (now, rate)  # type: ignore[attr-defined]
        return rate
    except Exception:
        # 失敗したら前回値があればそれを返す
        if cache:
            return cache[1]
        return None


# ------- 通貨 → JPY 変換レート（簡易キャッシュ付き） -------
_FX_CACHE: Dict[str, Tuple[float, float]] = {}  # currency -> (ts, rate_to_jpy)


def _get_fx_to_jpy(currency: str, ttl: int = 15 * 60) -> Optional[float]:
    """
    通貨コードを JPY に換算するレートを返す。

    - "JPY" または空文字: 1.0
    - "USD": yfinance("JPY=X") から 1USD=何円かを取得
    - それ以外: いまのところ 1.0 扱い（将来 EUR/HKD 等を増やす余地あり）

    ttl: キャッシュ有効時間（秒）
    """
    cur = (currency or "").upper()
    if cur in ("", "JPY"):
        return 1.0

    now = time.time()
    cache = _FX_CACHE.get(cur)
    if cache and (now - cache[0] < ttl):
        return cache[1]

    # 通貨ごとの yfinance シンボル
    pair_map = {
        "USD": "JPY=X",
        # 将来 EUR/HKD 等を足したくなったらここに追加:
        # "EUR": "EURJPY=X",
        # "HKD": "HKDJPY=X",
    }
    symbol = pair_map.get(cur)
    if not symbol:
        # 未対応通貨は 1.0 扱い（JPY相当）にしておく
        return cache[1] if cache else 1.0

    try:
        df = yf.download(
            symbol,
            period="5d",
            interval="1d",
            auto_adjust=False,
            progress=False,
        )
        if df is None or df.empty:
            return cache[1] if cache else 1.0

        close = df["Close"].dropna()
        if close.empty:
            return cache[1] if cache else 1.0

        rate = float(close.iloc[-1])  # 1 通貨 = rate JPY
        _FX_CACHE[cur] = (now, rate)
        return rate
    except Exception:
        return cache[1] if cache else 1.0


# ------- Holding を「同じ銘柄・同じ口座」でまとめるロジック -------
def _merge_existing_holding(new_obj: Holding) -> Optional[Holding]:
    """
    new_obj（まだ save していない 1トレード分の保有）を、
    すでに存在する Holding にまとめて平均取得単価＆平均FXを更新する。

    まとめるキー:
      user / broker / account / ticker / market / currency / side

    戻り値:
      統合先の Holding （見つからなければ None）
    """
    key = dict(
        user=new_obj.user,
        broker=new_obj.broker,
        account=new_obj.account,
        ticker=new_obj.ticker,
        market=new_obj.market,
        currency=new_obj.currency,
        side=new_obj.side,
    )
    existing = (
        Holding.objects.filter(**key)
        .order_by("opened_at", "id")
        .first()
    )
    if not existing:
        return None

    # 既存・新規の数量
    q_old = int(existing.quantity or 0)
    q_new = int(new_obj.quantity or 0)
    if q_new <= 0:
        return existing
    q_total = q_old + q_new
    if q_total <= 0:
        return existing

    # 単価（通貨建て）の加重平均
    p_old = Decimal(existing.avg_cost or 0)
    p_new = Decimal(new_obj.avg_cost or 0)
    total_cost_ccy = p_old * q_old + p_new * q_new
    existing.avg_cost = total_cost_ccy / Decimal(q_total)

    # FXレート（USDなど）の加重平均
    cur = (existing.currency or new_obj.currency or "JPY").upper()
    if cur != "JPY":
        def _fx_to_dec(v) -> Decimal:
            if v:
                try:
                    return Decimal(v)
                except Exception:
                    pass
            # 未入力なら現在レートで補完（最後の手段）
            rate = _get_fx_to_jpy(cur) or 0.0
            return Decimal(str(rate))

        fx_old = _fx_to_dec(existing.fx_rate)
        fx_new = _fx_to_dec(new_obj.fx_rate)

        total_cost_jpy = (p_old * fx_old * q_old) + (p_new * fx_new * q_new)
        if total_cost_ccy > 0:
            existing.fx_rate = total_cost_jpy / total_cost_ccy
    else:
        # 日本株は fx_rate を特に持たなくてOK
        existing.fx_rate = None

    # 数量と開始日
    existing.quantity = q_total
    op_old = existing.opened_at
    op_new = new_obj.opened_at
    if op_old and op_new:
        existing.opened_at = min(op_old, op_new)
    else:
        existing.opened_at = op_old or op_new

    # メモは簡単に連結
    if new_obj.memo:
        if existing.memo:
            existing.memo = f"{existing.memo}\n{new_obj.memo}"
        else:
            existing.memo = new_obj.memo

    existing.save()
    return existing


def _norm_ticker(raw: str) -> str:
    """
    '8591' / '186A' / 'AAPL' を trend._normalize_ticker に丸投げ。
    """
    return svc_trend._normalize_ticker(str(raw or ""))


def _today_jst() -> date:
    return date.today()


@dataclass
class RowVM:
    obj: Holding
    valuation: Optional[float] = None     # ★ 評価額（JPY）
    pnl: Optional[float] = None           # ★ 含み損益（JPY）
    pnl_pct: Optional[float] = None
    days: Optional[int] = None

    # 取得額（JPYベース）
    acq_jpy: Optional[float] = None       # ★ 追加：ポジション取得額（JPY）

    # ▼ 追加（配当・利回り表示用）
    price_now: Optional[float] = None     # 現在値（現地通貨）
    yield_now: Optional[float] = None
    yield_cost: Optional[float] = None
    div_annual: Optional[float] = None
    div_received: Optional[float] = None

    # スパークデータ
    s7_idx: Optional[List[float]] = None
    s30_idx: Optional[List[float]] = None
    s90_idx: Optional[List[float]] = None
    s7_raw: Optional[List[float]] = None
    s30_raw: Optional[List[float]] = None
    s90_raw: Optional[List[float]] = None


def _build_rows_for_queryset(qs) -> List[RowVM]:
    holdings = list(qs)
    tickers = [h.ticker for h in holdings]
    try:
        _preload_closes(tickers, 7)
        _preload_closes(tickers, 30)
        _preload_closes(tickers, 90)
    except Exception:
        pass
    return [_build_row(h) for h in holdings]


# ------- yfinance 価格バッチ取得（15分キャッシュ） -------
_SPARK_CACHE: Dict[Tuple[str, int], Tuple[float, List[float]]] = {}


def _cache_get(ticker_norm: str, days: int) -> Optional[List[float]]:
    item = _SPARK_CACHE.get((ticker_norm, days))
    if not item:
        return None
    ts, arr = item
    if time.time() - ts < 15 * 60:
        return arr
    return None


def _cache_put(ticker_norm: str, days: int, closes: List[float]) -> None:
    _SPARK_CACHE[(ticker_norm, days)] = (time.time(), closes)


def _infer_ex_date(div_date: date, ticker_norm: str) -> date:
    if ticker_norm.endswith(".T"):
        delta = 60
        delta = max(30, min(90, delta))
        return div_date - timedelta(days=delta)
    return div_date


def _preload_closes(tickers: List[str], days: int) -> Dict[str, List[float]]:
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
                    if (nsym, "Close") in df.columns:
                        s = df[(nsym, "Close")]
                    else:
                        try:
                            s = df.xs(nsym, axis=1)["Close"]  # type: ignore[index]
                        except Exception:
                            return []
                else:
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


# ------- yfinance 配当Series（1株あたり）取得（15分キャッシュ） -------
_DIV_CACHE: Dict[str, Tuple[float, List[Tuple[date, float]]]] = {}


def _get_dividends_1share(ticker_raw: str) -> List[Tuple[date, float]]:
    n = _norm_ticker(ticker_raw)
    cached = _DIV_CACHE.get(n)
    if cached and (time.time() - cached[0] < 15 * 60):
        return cached[1]

    out: List[Tuple[date, float]] = []
    try:
        s = yf.Ticker(n).dividends
        if s is not None and len(s) > 0:
            s = s.dropna()
            for ts, amt in s.items():
                try:
                    out.append((ts.date(), float(amt)))
                except Exception:
                    continue
    except Exception:
        out = []

    _DIV_CACHE[n] = (time.time(), out)
    return out


# ------- 年間配当（税引後合計：直近365日） -------
def _calc_div_annual_net(h: Holding) -> Optional[float]:
    try:
        since = _today_jst() - timedelta(days=365)

        # 1) 手動記録
        rel = getattr(h, "dividends", None)
        if rel:
            total = 0.0
            for d in rel.filter(date__gte=since):
                total += float(d.net_amount())
            if total > 0:
                return total

        # 2) 市場データから簡易推定
        qty = int(h.quantity or 0)
        if qty <= 0:
            return None

        divs = _get_dividends_1share(h.ticker)
        if not divs:
            return None

        acc = (h.account or "SPEC").upper()

        def _net(gross: float) -> float:
            if acc == "NISA":
                return gross
            elif acc == "MARGIN":
                return 0.0
            else:
                return gross * (1.0 - 0.20315)

        tnorm = _norm_ticker(h.ticker)
        total = 0.0
        for paid_or_ex, per_share in divs:
            ex_date = _infer_ex_date(paid_or_ex, tnorm)
            if ex_date >= since:
                total += _net(per_share * qty)

        return total if total > 0 else None
    except Exception:
        return None


def _build_row(h: Holding) -> RowVM:
    q = int(h.quantity or 0)
    cost_unit = _to_float(h.avg_cost or 0) or 0.0

    # 通貨判定（デフォルトは JPY）→ JPY への換算レート取得
    cur = (getattr(h, "currency", "JPY") or "JPY").upper()
    fx = _get_fx_to_jpy(cur) or 1.0   # 1 通貨 = fx JPY
    is_usd = (cur == "USD")

    n = _norm_ticker(h.ticker)
    raw7 = _preload_closes([h.ticker], 7).get(n, [])
    raw30 = _preload_closes([h.ticker], 30).get(n, [])
    raw90 = _preload_closes([h.ticker], 90).get(n, [])

    # ===== 現在値・評価額 =====
    price_now: Optional[float] = None          # 現地通貨ベースの 1株価格
    val_now_jpy: Optional[float] = None        # JPY ベースの評価額

    if raw30 or raw7 or raw90:
        last = (raw30 or raw7 or raw90)[-1]
        price_now = float(last)                # 現地通貨の終値

        if q > 0:
            # ★ 評価額は常に JPY ベースに統一
            val_now_jpy = price_now * fx * q

    # ===== 含み損益（BUY/SELL 両対応, JPYベース）=====
    pnl_jpy: Optional[float] = None
    pnl_pct: Optional[float] = None
    if price_now is not None and cost_unit > 0 and q > 0:
        # コスト/現在値を JPY に換算
        cost_unit_jpy = cost_unit * fx
        price_now_jpy = price_now * fx

        side = (getattr(h, "side", "BUY") or "BUY").upper()
        if side == "SELL":
            # 空売り：価格が下がるとプラス
            pnl_jpy = (cost_unit_jpy - price_now_jpy) * q
        else:
            # 買い：価格が上がるとプラス
            pnl_jpy = (price_now_jpy - cost_unit_jpy) * q

        acq_jpy = cost_unit_jpy * q
        if acq_jpy > 0:
            pnl_pct = (pnl_jpy / acq_jpy) * 100.0

    # ===== 配当系（従来どおり現地通貨ベース） =====
    div_annual = _calc_div_annual_net(h)

    y_now = y_cost = None
    if div_annual is not None and q > 0:
        div_ps = div_annual / q
        if price_now and price_now > 0:
            y_now = (div_ps / price_now) * 100.0
        if cost_unit > 0:
            y_cost = (div_ps / cost_unit) * 100.0

    div_received = None
    try:
        opened = h.opened_at or (h.created_at.date() if h.created_at else None)
        if opened and q > 0:
            divs = _get_dividends_1share(h.ticker)
            if divs:
                acc = (h.account or "SPEC").upper()

                def _net(gross: float) -> float:
                    if acc == "NISA":
                        return gross
                    elif acc == "MARGIN":
                        return 0.0
                    else:
                        return gross * (1.0 - 0.20315)

                tnorm = _norm_ticker(h.ticker)
                tot = 0.0
                for paid_or_ex, per_share in divs:
                    ex_date = _infer_ex_date(paid_or_ex, tnorm)
                    if ex_date >= opened:
                        tot += _net(per_share * q)
                if tot > 0:
                    div_received = tot
    except Exception:
        pass

    start = h.opened_at or (h.created_at.date() if h.created_at else None)
    days = (_today_jst() - start).days if start else None

    def _idx(arr: List[float]) -> List[float]:
        if not arr:
            return []
        base = arr[0] or 0.0
        return [round(v / base, 4) if base else 1.0 for v in arr]

    s7_idx = _idx(raw7)
    s30_idx = _idx(raw30)
    s90_idx = _idx(raw90)

    return RowVM(
        obj=h,
        valuation=val_now_jpy,   # ★ JPY ベース
        pnl=pnl_jpy,             # ★ JPY ベース
        pnl_pct=pnl_pct,
        days=days,
        price_now=price_now,     # 現地通貨
        yield_now=y_now,
        yield_cost=y_cost,
        div_annual=div_annual,
        div_received=div_received,
        s7_idx=s7_idx or None,
        s30_idx=s30_idx or None,
        s90_idx=s90_idx or None,
        s7_raw=raw7 or None,
        s30_raw=raw30 or None,
        s90_raw=raw90 or None,
    )


def _aggregate(rows: List[RowVM]) -> Dict[str, Optional[float]]:
    n = 0
    acq_sum_jpy = 0.0
    val_sum_jpy = 0.0
    have_val = 0
    winners = losers = 0
    days_list: List[int] = []
    top_gain: Optional[Tuple[float, Holding]] = None
    top_loss: Optional[Tuple[float, Holding]] = None

    pnl_sum_acc_jpy = 0.0
    have_pnl = False

    for r in rows:
        h = r.obj
        n += 1

        # 通貨判定（取得額だけここで JPY 換算）
        cur = getattr(h, "currency", "JPY") or "JPY"
        fx = _get_fx_to_jpy(cur) or 1.0  # 1通貨 = fx JPY

        # 取得金額（その通貨建て）→ JPY へ
        q = int(h.quantity or 0)
        cost = _to_float(h.avg_cost or 0) or 0.0
        acq_i_native = q * cost
        acq_sum_jpy += acq_i_native * fx

        # 評価額は RowVM 側で既に JPY ベースに統一済み
        if r.valuation is not None:
            val_sum_jpy += float(r.valuation)
            have_val += 1

        # 含み損益も RowVM 側で JPY ベースに統一済み
        if r.pnl is not None:
            pnl_jpy = float(r.pnl)
            pnl_sum_acc_jpy += pnl_jpy
            have_pnl = True

            if pnl_jpy > 0:
                winners += 1
            elif pnl_jpy < 0:
                losers += 1

            if top_gain is None or pnl_jpy > top_gain[0]:
                top_gain = (pnl_jpy, h)
            if top_loss is None or pnl_jpy < top_loss[0]:
                top_loss = (pnl_jpy, h)

        # 保有日数
        if r.days is not None:
            days_list.append(int(r.days))

    # ★ ポートフォリオ含み損益は JPY 合計をそのまま使う
    pnl_sum: Optional[float] = pnl_sum_acc_jpy if have_pnl else None
    pnl_pct: Optional[float] = (
        pnl_sum / acq_sum_jpy * 100.0
        if (pnl_sum is not None and acq_sum_jpy > 0)
        else None
    )
    win_rate: Optional[float] = (
        winners / (winners + losers) * 100.0
        if (winners + losers) > 0
        else None
    )

    avg_days: Optional[float] = (sum(days_list) / len(days_list)) if days_list else None
    med_days: Optional[float] = (median(days_list) if days_list else None)
    avg_pos_size: Optional[float] = (acq_sum_jpy / n) if n else None

    summary: Dict[str, Optional[float]] = dict(
        count=n,
        acq=acq_sum_jpy,
        val=val_sum_jpy if have_val else None,
        pnl=pnl_sum,
        pnl_pct=pnl_pct,
        winners=winners,
        losers=losers,
        win_rate=win_rate,
        avg_days=avg_days,
        med_days=med_days,
        avg_pos_size=avg_pos_size,
    )
    if top_gain:
        summary["top_gain_pnl"] = top_gain[0]
        summary["top_gain_id"] = top_gain[1].id
    if top_loss:
        summary["top_loss_pnl"] = top_loss[0]
        summary["top_loss_id"] = top_loss[1].id
    return summary


# =========================================================
# API: コード→銘柄名 + セクター（33業種）
# =========================================================
@login_required
def api_ticker_name(request):
    raw = (request.GET.get("code") or request.GET.get("q") or "").strip()
    norm = svc_trend._normalize_ticker(raw)
    code = (norm.split(".", 1)[0] if norm else raw).upper()

    name = ""
    sector_hint = None

    # ① 上書き辞書
    override = getattr(settings, "TSE_NAME_OVERRIDES", {}).get(code)
    if override:
        name = override

    # ② JPXマスタ連携（日本株コードのとき）
    if code.isdigit():
        m_name, m_sector = svc_trend.lookup_master_name_and_sector(norm)
        if m_name and not name:
            name = m_name
        if m_sector:
            sector_hint = m_sector

    # ③ まだ名前が空なら、従来のリスト / yfinance で補完
    if not name:
        name = svc_trend._lookup_name_jp_from_list(norm) or ""
        if not name:
            try:
                name = svc_trend._fetch_name_prefer_jp(norm) or ""
            except Exception:
                name = ""

    # ===== セクター（33業種） =====
    # 0) キャッシュ
    cached = _sector_cache_get(norm)
    if cached:
        sector = cached
    else:
        sector = None

        # 1) まず JPXマスタから取れたセクターヒント
        if sector_hint:
            sector = sector_hint

        # 2) それでも無ければ、既存の prefer_jp（高品質）
        if not sector:
            try:
                sector = svc_trend._fetch_sector_prefer_jp(norm) or None
            except Exception:
                sector = None

        # 3) さらにダメなら yfinance の info から英語Sector/Industryを取得して簡易マッピング
        if not sector:
            try:
                info = yf.Ticker(norm).get_info()
                sec_en = (info or {}).get("sector") or (info or {}).get("industry") or ""
                map_en2jp = {
                    "Technology": "情報・通信業",
                    "Communication Services": "情報・通信業",
                    "Industrials": "機械",
                    "Consumer Cyclical": "小売業",
                    "Consumer Defensive": "食料品",
                    "Financial Services": "銀行業",
                    "Real Estate": "不動産業",
                    "Healthcare": "医薬品",
                    "Basic Materials": "化学",
                    "Energy": "石油・石炭製品",
                    "Utilities": "電気・ガス業",
                }
                sector = map_en2jp.get(str(sec_en), str(sec_en)) or None
            except Exception:
                sector = None

        # 4) 取れたものをキャッシュ
        if sector:
            _sector_cache_put(norm, sector)

    return JsonResponse({"code": code, "name": name, "sector": sector or ""})


# =========================================================
# 一覧（フィルタ/並び替え/ページング）
# =========================================================
def _apply_filters(qs, request):
    def _normalize_choice(field_name: str, raw: str) -> Optional[str]:
        if raw is None:
            return None
        s = str(raw).strip()
        if s == "" or s.upper() == "ALL" or s == "すべて":
            return None

        import unicodedata as _ud
        key = _ud.normalize("NFKC", s).strip()

        field = Holding._meta.get_field(field_name)
        for value, label in (field.choices or []):
            v = str(value)
            l = _ud.normalize("NFKC", str(label)).strip()
            if key == v or key == l:
                return value
        return s

    broker = _normalize_choice("broker", request.GET.get("broker"))
    account = _normalize_choice("account", request.GET.get("account"))
    side = _normalize_choice("side", request.GET.get("side"))

    if broker:
        qs = qs.filter(broker=broker)
    if account:
        qs = qs.filter(account=account)
    if side:
        qs = qs.filter(side=side)

    q = (request.GET.get("q") or request.GET.get("ticker") or "").strip()
    if q:
        qs = qs.filter(models.Q(ticker__icontains=q) | models.Q(name__icontains=q))

    return qs


def _sort_qs(qs, request):
    sort = request.GET.get("sort") or "updated"  # updated|created|opened
    order = request.GET.get("order") or "desc"  # asc|desc
    if sort in ("updated", "created", "opened"):
        field = {"updated": "updated_at", "created": "created_at", "opened": "opened_at"}[sort]
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
    pnl_sign = (request.GET.get("pnl") or "").upper()  # POS|NEG|""(all)
    if pnl_sign == "POS":
        rows = [r for r in rows if (r.pnl or 0) > 0]
    elif pnl_sign == "NEG":
        rows = [r for r in rows if (r.pnl or 0) < 0]
    return rows


def _sort_rows(rows: List[RowVM], request) -> List[RowVM]:
    sort = (request.GET.get("sort") or "").lower()
    order = (request.GET.get("order") or "desc").lower()
    reverse = order != "asc"

    if sort == "pnl":
        rows.sort(key=lambda r: (r.pnl is None, r.pnl or 0.0), reverse=reverse)
    elif sort == "days":
        rows.sort(key=lambda r: (r.days is None, r.days or 0), reverse=reverse)
    return rows


@login_required
def holding_list(request):
    qs = Holding.objects.filter(user=request.user).prefetch_related("dividends")
    qs = _apply_filters(qs, request)
    qs = _sort_qs(qs, request)

    page = _page(request, qs)
    rows_page = _build_rows_for_page(page)
    rows_page = _apply_post_filters(rows_page, request)
    rows_page = _sort_rows(rows_page, request)

    rows_all = _build_rows_for_queryset(qs)
    rows_all = _apply_post_filters(rows_all, request)
    summary = _aggregate(rows_all)
    summary["count"] = qs.count()
    summary["page_count"] = len(rows_page)

    class _PageWrap:
        def __init__(self, src, objs):
            self.number = src.number
            self.paginator = src.paginator
            self.has_previous = src.has_previous
            self.has_next = src.has_next
            self.previous_page_number = src.previous_page_number
            self.next_page_number = src.next_page_number
            self.object_list = objs

    page_wrap = _PageWrap(page, rows_page)

    ctx = {
        "page": page_wrap,
        "sort": request.GET.get("sort") or "updated",
        "order": request.GET.get("order") or "desc",
        "filters": {
            "broker": request.GET.get("broker") or "",
            "account": request.GET.get("account") or "",
            "ticker": request.GET.get("ticker") or "",
            "side": request.GET.get("side") or "",
            "pnl": request.GET.get("pnl") or "",
        },
        "summary": summary,
    }
    return render(request, "holdings/list.html", ctx)


@login_required
def holding_list_partial(request):
    qs = Holding.objects.filter(user=request.user).prefetch_related("dividends")
    qs = _apply_filters(qs, request)
    qs = _sort_qs(qs, request)

    page = _page(request, qs)
    rows_page = _build_rows_for_page(page)
    rows_page = _apply_post_filters(rows_page, request)
    rows_page = _sort_rows(rows_page, request)

    rows_all = _build_rows_for_queryset(qs)
    rows_all = _apply_post_filters(rows_all, request)
    summary = _aggregate(rows_all)
    summary["count"] = qs.count()
    summary["page_count"] = len(rows_page)

    class _PageWrap:
        def __init__(self, src, objs):
            self.number = src.number
            self.paginator = src.paginator
            self.has_previous = src.has_previous
            self.has_next = src.has_next
            self.previous_page_number = src.previous_page_number
            self.next_page_number = src.next_page_number
            self.object_list = objs

    page_wrap = _PageWrap(page, rows_page)

    ctx = {
        "page": page_wrap,
        "sort": request.GET.get("sort") or "updated",
        "order": request.GET.get("order") or "desc",
        "filters": {
            "broker": request.GET.get("broker") or "",
            "account": request.GET.get("account") or "",
            "ticker": request.GET.get("ticker") or "",
            "side": request.GET.get("side") or "",
            "pnl": request.GET.get("pnl") or "",
        },
        "summary": summary,
    }
    return render(request, "holdings/_list.html", ctx)


# =========================================================
# 作成/編集/削除
# =========================================================
@login_required
def holding_create(request):
    if request.method == "POST":
        form = HoldingForm(request.POST)
        if form.is_valid():
            obj = form.save(commit=False)
            obj.user = request.user

            # ★ ここでティッカーから market / currency を自動判定
            head = (obj.ticker or "").upper()
            if head.isalpha():
                # 英字だけ → 米国株 / USD
                obj.market = "US"
                obj.currency = "USD"
            else:
                # 数字のみ or 数字＋英字(186Aなど) → 日本株 / JPY
                obj.market = "JP"
                obj.currency = "JPY"

            # ★ 同じ銘柄＋同じ口座の既存保有があればまとめる
            merged = _merge_existing_holding(obj)
            if merged:
                messages.success(
                    request,
                    "同じ銘柄の既存保有に買い足しとして反映し、平均取得を更新しました。",
                )
            else:
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
            obj = form.save(commit=False)

            # ★ 編集時もティッカー基準で毎回 market / currency を再セット
            head = (obj.ticker or "").upper()
            if head.isalpha():
                obj.market = "US"
                obj.currency = "USD"
            else:
                obj.market = "JP"
                obj.currency = "JPY"

            obj.save()
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