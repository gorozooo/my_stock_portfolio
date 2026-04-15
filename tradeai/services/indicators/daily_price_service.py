# =========================================================
# [FILE] daily_price_service.py
# [PATH] <project_root>/tradeai/services/indicators/daily_price_service.py
#
# このファイルは何？
# - 日足のOHLCデータを取得するサービスです。
# - 保有監視で GC/DC や ATR を計算するための元データを返します。
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
        df = df.dropna(subset=["High", "Low", "Close"])
    except Exception:
        _PRICE_CACHE[symbol] = (now_ts, None)
        return None

    if df.empty:
        _PRICE_CACHE[symbol] = (now_ts, None)
        return None

    result = {
        "symbol": symbol,
        "dates": [idx.to_pydatetime() for idx in df.index],
        "highs": [float(v) for v in df["High"].tolist()],
        "lows": [float(v) for v in df["Low"].tolist()],
        "closes": [float(v) for v in df["Close"].tolist()],
        "last_close": float(df["Close"].iloc[-1]),
    }

    _PRICE_CACHE[symbol] = (now_ts, result)
    return result