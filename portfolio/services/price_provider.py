# portfolio/services/price_provider.py
from __future__ import annotations
from typing import Dict, Iterable, List
import time

from django.db.models import Max
from portfolio.models import RealizedTrade  # 直近約定のフォールバックに使う

# 5分キャッシュの超軽量実装
_CACHE: Dict[str, float] = {}
_CACHE_TS: float = 0.0
_CACHE_TTL_SEC = 300


def _now_cache_valid() -> bool:
    return (time.time() - _CACHE_TS) < _CACHE_TTL_SEC


def _fallback_with_trades(tickers: List[str]) -> Dict[str, float]:
    """
    RealizedTrade から ticker ごとの直近価格を拾う。
    """
    out: Dict[str, float] = {}
    if not tickers:
        return out
    # 最新のtrade_atごとに price を採用
    latest = (
        RealizedTrade.objects.filter(ticker__in=tickers)
        .values("ticker")
        .annotate(lat=Max("trade_at"))
    )
    lat_map = {r["ticker"].upper(): r["lat"] for r in latest if r["lat"]}

    if not lat_map:
        return out

    rows = RealizedTrade.objects.filter(
        ticker__in=list(lat_map.keys()), trade_at__in=list(lat_map.values())
    ).values("ticker", "price")
    for r in rows:
        try:
            out[(r["ticker"] or "").upper()] = float(r["price"])
        except Exception:
            pass
    return out


def get_prices(tickers_in: Iterable[str]) -> Dict[str, float]:
    """
    可能なら yfinance を使用し、それ以外は直近の約定価格で補完。
    返らない銘柄は呼び出し側で avg_cost にフォールバックする。
    """
    global _CACHE_TS, _CACHE
    tickers = sorted({(t or "").strip().upper() for t in tickers_in if t})
    if not tickers:
        return {}

    # キャッシュ命中
    if _now_cache_valid():
        had = {t: _CACHE[t] for t in tickers if t in _CACHE}
        if len(had) == len(tickers):
            return had

    out: Dict[str, float] = {}

    # 1) yfinance
    try:
        import yfinance as yf  # type: ignore

        data = yf.download(" ".join(tickers), period="1d", interval="1m", progress=False)
        # 形状差異を吸収
        if hasattr(data, "empty") and not data.empty:
            try:
                # 複数銘柄: MultiIndex ("Close", "TICKER")
                last = data.tail(1)
                # "Close" が一次カラムにあるケース
                if "Close" in last.columns:
                    row = last["Close"]
                    for t in tickers:
                        try:
                            v = float(row[t].values[-1])
                            if v > 0:
                                out[t] = v
                        except Exception:
                            pass
                else:
                    for t in tickers:
                        try:
                            v = float(last["Close"][t].values[-1])
                            if v > 0:
                                out[t] = v
                        except Exception:
                            pass
            except Exception:
                # 単一銘柄などの形状
                try:
                    v = float(data["Close"].dropna().iloc[-1])
                    if v > 0:
                        out[tickers[0]] = v
                except Exception:
                    pass
    except Exception:
        # yfinance が無い/失敗 → 何もしない
        pass

    # 2) 足りない銘柄は約定価格で補完
    missing = [t for t in tickers if t not in out]
    if missing:
        out.update(_fallback_with_trades(missing))

    # キャッシュへ
    if out:
        for k, v in out.items():
            _CACHE[k] = v
        _CACHE_TS = time.time()
    return out