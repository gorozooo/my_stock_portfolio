"""
[FILE] autotrade/services/universe/metrics.py
[PATH] <project_root>/autotrade/services/universe/metrics.py

このファイルは何？
- 日足データから「流動性（売買代金）」と「ボラ（ATR%）」を計算する部品です。
- 200銘柄を扱うときの“第1関門”で、ここが速さと安定の中心になります。

キャッシュの置き場所
- media/autotrade/cache/daily_1d/<ticker>.csv

初心者ポイント
- 日足は1日1回しか変わらないので、キャッシュが効けばかなり速くなります。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional, Dict

import pandas as pd
import yfinance as yf
from django.conf import settings


@dataclass
class DailyMetrics:
    ticker: str
    last_close: Optional[float] = None

    # 流動性（売買代金）
    avg_dv_yen: Optional[float] = None  # average close*volume

    # ボラ（ATR%）
    atr_pct: Optional[float] = None     # ATR / close

    # 参考
    avg_volume: Optional[float] = None
    note: str = ""


def _cache_dir() -> str:
    base = getattr(settings, "MEDIA_ROOT", "media")
    d = os.path.join(base, "autotrade", "cache", "daily_1d")
    os.makedirs(d, exist_ok=True)
    return d


def _cache_path(ticker: str) -> str:
    safe = ticker.replace("/", "_")
    return os.path.join(_cache_dir(), f"{safe}.csv")


def _read_cache(path: str) -> pd.DataFrame:
    if not os.path.exists(path):
        return pd.DataFrame()
    try:
        df = pd.read_csv(path)
        if df.empty:
            return df
        if "dt" in df.columns:
            df["dt"] = pd.to_datetime(df["dt"])
            df = df.set_index("dt")
        return df
    except Exception:
        return pd.DataFrame()


def _write_cache(path: str, df: pd.DataFrame) -> None:
    if df is None or df.empty:
        return
    out = df.copy()
    out = out.reset_index().rename(columns={"index": "dt"})
    if "Date" in out.columns:
        out = out.rename(columns={"Date": "dt"})
    if "dt" not in out.columns:
        out.insert(0, "dt", df.index.astype(str))
    out.to_csv(path, index=False, encoding="utf-8")


def fetch_daily_1d(ticker: str, period: str = "180d", use_cache: bool = True) -> pd.DataFrame:
    path = _cache_path(ticker)
    if use_cache:
        cached = _read_cache(path)
        if not cached.empty:
            return cached

    df = yf.download(ticker, interval="1d", period=period, auto_adjust=False, progress=False)
    if df is None or df.empty:
        return pd.DataFrame()

    df = df.rename(columns={
        "Open": "open", "High": "high", "Low": "low", "Close": "close", "Volume": "volume"
    }).dropna()

    _write_cache(path, df)
    return df


def _atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    """
    True Range:
      TR = max(high-low, abs(high-prev_close), abs(low-prev_close))
    ATR = rolling mean(TR, n)
    """
    prev_close = df["close"].shift(1)
    tr1 = (df["high"] - df["low"]).abs()
    tr2 = (df["high"] - prev_close).abs()
    tr3 = (df["low"] - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.rolling(n).mean()


def compute_daily_metrics(ticker: str, lookback_days: int = 60, use_cache: bool = True) -> DailyMetrics:
    """
    直近 lookback_days から
    - avg_dv_yen = mean(close*volume)
    - atr_pct    = ATR14 / last_close
    """
    dm = DailyMetrics(ticker=ticker)
    df = fetch_daily_1d(ticker, use_cache=use_cache)

    if df is None or df.empty:
        dm.note = "no_data"
        return dm

    if not all(x in df.columns for x in ["high", "low", "close", "volume"]):
        dm.note = "missing_cols"
        return dm

    df = df.tail(max(lookback_days, 30)).copy()
    if df.empty or len(df) < 20:
        dm.note = "too_short"
        return dm

    last_close = float(df["close"].iloc[-1])
    dm.last_close = last_close

    dv = (df["close"] * df["volume"]).astype(float)
    dm.avg_dv_yen = float(dv.mean())

    dm.avg_volume = float(df["volume"].astype(float).mean())

    atr14 = _atr(df, n=14)
    if atr14 is None or atr14.dropna().empty or last_close <= 0:
        dm.atr_pct = None
        dm.note = "atr_na"
        return dm

    atr_val = float(atr14.dropna().iloc[-1])
    dm.atr_pct = float(atr_val / last_close) if last_close > 0 else None

    return dm
