# =========================================================
# [FILE] daily_price_service.py
# [PATH] <project_root>/tradeai/services/indicators/daily_price_service.py
#
# このファイルは何？
# - 日足のOHLCVデータを取得するサービスです。
# - GC/DC, ATR, RSI, MACD, VWAP, 出来高急増, ブレイク判定の元データを返します。
# =========================================================

from __future__ import annotations

import time
from typing import Any

import yfinance as yf


CACHE_TTL_SEC = 300
_PRICE_CACHE: dict[str, tuple[float, dict[str, Any] | None]] = {}


def _to_yfinance_symbol(raw: str) -> str:
    value = (raw or "").strip().upper()
    if value.endswith(".T"):
        return value
    if value.isdigit():
        return f"{value}.T"
    return value


def get_daily_ohlc(ticker: str, period: str = "6mo") -> dict[str, Any] | None:
    symbol = _to_yfinance_symbol(ticker)
    now_ts = time.time()

    cached = _PRICE_CACHE.get(symbol)
    if cached:
        cached_ts, cached_value = cached
        if now_ts - cached_ts < CACHE_TTL_SEC:
            return cached_value

    try:
        df = yf.Ticker(symbol).history(period=period, interval="1d", auto_adjust=False)
    except Exception:
        _PRICE_CACHE[symbol] = (now_ts, None)
        return None

    if df is None or df.empty:
        _PRICE_CACHE[symbol] = (now_ts, None)
        return None

    try:
        df = df.dropna(subset=["Open", "High", "Low", "Close"])
    except Exception:
        _PRICE_CACHE[symbol] = (now_ts, None)
        return None

    if df.empty:
        _PRICE_CACHE[symbol] = (now_ts, None)
        return None

    opens = [float(v) for v in df["Open"].fillna(0).tolist()]
    highs = [float(v) for v in df["High"].fillna(0).tolist()]
    lows = [float(v) for v in df["Low"].fillna(0).tolist()]
    closes = [float(v) for v in df["Close"].fillna(0).tolist()]
    volumes = [float(v) for v in df["Volume"].fillna(0).tolist()] if "Volume" in df.columns else [0.0] * len(df)

    result = {
        "symbol": symbol,
        "dates": [idx.to_pydatetime() for idx in df.index],
        "opens": opens,
        "highs": highs,
        "lows": lows,
        "closes": closes,
        "volumes": volumes,
        "last_open": opens[-1] if opens else None,
        "last_close": closes[-1] if closes else None,
        "last_high": highs[-1] if highs else None,
        "last_low": lows[-1] if lows else None,
        "last_volume": volumes[-1] if volumes else None,
    }

    _PRICE_CACHE[symbol] = (now_ts, result)
    return result