# [FILE] valuation_service.py
# [PATH] portfolio/services/holdings/valuation_service.py
#
# このファイルは何？
# - Holding 一覧で使う評価・集計ロジックをまとめた service
# - 価格取得、為替取得、配当利回り、スパークライン、集計を担当する
#
# 今回の目的
# - views/holding.py を薄くする
# - 一覧表示ロジックをここへ寄せる

# -*- coding: utf-8 -*-
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from statistics import median
from typing import Dict, List, Optional, Tuple, Union
import time

import pandas as pd
import yfinance as yf

from ...models import Holding
from .. import trend as svc_trend

Number = Union[int, float, Decimal]

SECTOR_CACHE_TTL = 30 * 60
_SPARK_CACHE: Dict[Tuple[str, int], Tuple[float, List[float]]] = {}
_DIV_CACHE: Dict[str, Tuple[float, List[Tuple[date, float]]]] = {}
_FX_CACHE: Dict[str, Tuple[float, float]] = {}


def _today_jst() -> date:
    return date.today()


def _to_float(x) -> Optional[float]:
    try:
        if x is None:
            return None
        return float(x)
    except Exception:
        return None


def _norm_ticker(raw: str) -> str:
    return svc_trend._normalize_ticker(str(raw or ""))


def _get_fx_to_jpy(currency: str, ttl: int = 15 * 60) -> Optional[float]:
    cur = (currency or "").upper()
    if cur in ("", "JPY"):
        return 1.0

    now = time.time()
    cache = _FX_CACHE.get(cur)
    if cache and (now - cache[0] < ttl):
        return cache[1]

    pair_map = {
        "USD": "JPY=X",
    }
    symbol = pair_map.get(cur)
    if not symbol:
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

        rate = float(close.iloc[-1])
        _FX_CACHE[cur] = (now, rate)
        return rate
    except Exception:
        return cache[1] if cache else 1.0


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


def _calc_div_annual_net(h: Holding) -> Optional[float]:
    try:
        since = _today_jst() - timedelta(days=365)

        rel = getattr(h, "dividends", None)
        if rel:
            total = 0.0
            for d in rel.filter(date__gte=since):
                total += float(d.net_amount())
            if total > 0:
                return total

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


@dataclass
class RowVM:
    obj: Holding
    valuation: Optional[float] = None
    pnl: Optional[float] = None
    pnl_pct: Optional[float] = None
    days: Optional[int] = None

    acq_jpy: Optional[float] = None

    price_now: Optional[float] = None
    yield_now: Optional[float] = None
    yield_cost: Optional[float] = None
    div_annual: Optional[float] = None
    div_received: Optional[float] = None

    s7_idx: Optional[List[float]] = None
    s30_idx: Optional[List[float]] = None
    s90_idx: Optional[List[float]] = None
    s7_raw: Optional[List[float]] = None
    s30_raw: Optional[List[float]] = None
    s90_raw: Optional[List[float]] = None


def build_row(h: Holding) -> RowVM:
    q = int(h.quantity or 0)
    cost_unit = _to_float(h.avg_cost or 0) or 0.0

    cur = (getattr(h, "currency", "JPY") or "JPY").upper()
    fx = _get_fx_to_jpy(cur) or 1.0

    n = _norm_ticker(h.ticker)
    raw7 = _preload_closes([h.ticker], 7).get(n, [])
    raw30 = _preload_closes([h.ticker], 30).get(n, [])
    raw90 = _preload_closes([h.ticker], 90).get(n, [])

    price_now: Optional[float] = None
    val_now_jpy: Optional[float] = None

    if raw30 or raw7 or raw90:
        last = (raw30 or raw7 or raw90)[-1]
        price_now = float(last)
        if q > 0:
            val_now_jpy = price_now * fx * q

    pnl_jpy: Optional[float] = None
    pnl_pct: Optional[float] = None
    if price_now is not None and cost_unit > 0 and q > 0:
        cost_unit_jpy = cost_unit * fx
        price_now_jpy = price_now * fx

        side = (getattr(h, "side", "BUY") or "BUY").upper()
        if side == "SELL":
            pnl_jpy = (cost_unit_jpy - price_now_jpy) * q
        else:
            pnl_jpy = (price_now_jpy - cost_unit_jpy) * q

        acq_jpy = cost_unit_jpy * q
        if acq_jpy > 0:
            pnl_pct = (pnl_jpy / acq_jpy) * 100.0

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
        valuation=val_now_jpy,
        pnl=pnl_jpy,
        pnl_pct=pnl_pct,
        days=days,
        price_now=price_now,
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


def build_rows_for_queryset(qs) -> List[RowVM]:
    holdings = list(qs)
    tickers = [h.ticker for h in holdings]
    try:
        _preload_closes(tickers, 7)
        _preload_closes(tickers, 30)
        _preload_closes(tickers, 90)
    except Exception:
        pass
    return [build_row(h) for h in holdings]


def build_rows_for_page(page) -> List[RowVM]:
    return [build_row(h) for h in page.object_list]


def apply_post_filters(rows: List[RowVM], request) -> List[RowVM]:
    pnl_sign = (request.GET.get("pnl") or "").upper()
    if pnl_sign == "POS":
        rows = [r for r in rows if (r.pnl or 0) > 0]
    elif pnl_sign == "NEG":
        rows = [r for r in rows if (r.pnl or 0) < 0]
    return rows


def sort_rows(rows: List[RowVM], request) -> List[RowVM]:
    sort = (request.GET.get("sort") or "").lower()
    order = (request.GET.get("order") or "desc").lower()
    reverse = order != "asc"

    if sort == "pnl":
        rows.sort(key=lambda r: (r.pnl is None, r.pnl or 0.0), reverse=reverse)
    elif sort == "days":
        rows.sort(key=lambda r: (r.days is None, r.days or 0), reverse=reverse)
    return rows


def aggregate_rows(rows: List[RowVM]) -> Dict[str, Optional[float]]:
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

        cur = getattr(h, "currency", "JPY") or "JPY"
        fx = _get_fx_to_jpy(cur) or 1.0

        q = int(h.quantity or 0)
        cost = _to_float(h.avg_cost or 0) or 0.0
        acq_i_native = q * cost
        acq_sum_jpy += acq_i_native * fx

        if r.valuation is not None:
            val_sum_jpy += float(r.valuation)
            have_val += 1

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

        if r.days is not None:
            days_list.append(int(r.days))

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