"""
[FILE] autotrade/services/universe/metrics.py
[PATH] <project_root>/autotrade/services/universe/metrics.py

このファイルは何？
- 日足データから「流動性（売買代金）」と「ボラ（ATR%）」を計算する部品です。
- 200銘柄を扱うときの“第1関門”で、ここが速さと安定の中心になります。

このファイルが保証すること（重要）
- close / volume / high / low は「数値として計算できる状態」に正規化される
- 取得元が yfinance でも cache(CSV) でも、必ず同じ正規化ルールが適用される
- 数値にできない行は NaN にして除外される（上流に負担を押し付けない）

キャッシュの置き場所
- media/autotrade/cache/daily_1d/<ticker>.csv

初心者ポイント
- 取得元は信用せず、必ずここで“整形してから使う”のがコツです。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

import pandas as pd
import yfinance as yf
from django.conf import settings


@dataclass
class DailyMetrics:
    ticker: str
    last_close: Optional[float] = None

    # 流動性（売買代金）
    avg_dv_yen: Optional[float] = None  # average(close * volume)

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
            df["dt"] = pd.to_datetime(df["dt"], errors="coerce")
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


def _normalize_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    """
    取得元（yfinance/CSVキャッシュ）が何であっても、
    ここで「数値として安全なOHLCV」に統一する。
    """
    if df is None or df.empty:
        return pd.DataFrame()

    # 列名の揺れに対応（念のため）
    df = df.rename(columns={
        "Open": "open",
        "High": "high",
        "Low": "low",
        "Close": "close",
        "Volume": "volume",
    })

    for col in ["open", "high", "low", "close", "volume"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # 必須列が揃わない場合は空にする（上流で no_data 扱い）
    required = ["high", "low", "close", "volume"]
    if not all(c in df.columns for c in required):
        return pd.DataFrame()

    # 数値化できなかった行は落とす
    df = df.dropna(subset=required)

    # volume が 0 や負値は意味がないので落とす（保険）
    if "volume" in df.columns:
        df = df[df["volume"] > 0]

    return df


def fetch_daily_1d(ticker: str, period: str = "180d", use_cache: bool = True) -> pd.DataFrame:
    path = _cache_path(ticker)

    # 1) キャッシュ
    if use_cache:
        cached = _read_cache(path)
        cached = _normalize_ohlcv(cached)
        if not cached.empty:
            return cached

    # 2) 取得（yfinance）
    df = yf.download(
        ticker,
        interval="1d",
        period=period,
        auto_adjust=False,
        progress=False,
    )

    df = _normalize_ohlcv(df)
    if df is None or df.empty:
        return pd.DataFrame()

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


def compute_daily_metrics(
    ticker: str,
    lookback_days: int = 60,
    use_cache: bool = True,
) -> DailyMetrics:
    """
    直近 lookback_days から
    - avg_dv_yen = mean(close * volume)
    - atr_pct    = ATR14 / last_close
    """
    dm = DailyMetrics(ticker=ticker)

    df = fetch_daily_1d(ticker, use_cache=use_cache)

    if df is None or df.empty:
        dm.note = "no_data"
        return dm

    df = df.tail(max(int(lookback_days), 30)).copy()
    if df.empty or len(df) < 20:
        dm.note = "too_short"
        return dm

    last_close = df["close"].iloc[-1]
    if pd.isna(last_close):
        dm.note = "invalid_close"
        return dm

    last_close = float(last_close)
    if last_close <= 0:
        dm.note = "invalid_close"
        return dm

    dm.last_close = last_close

    # 売買代金（安全に計算）
    dv = (df["close"] * df["volume"]).dropna()
    if dv.empty:
        dm.note = "dv_na"
        return dm

    dm.avg_dv_yen = float(dv.mean())

    vol = df["volume"].dropna()
    dm.avg_volume = float(vol.mean()) if not vol.empty else None

    atr14 = _atr(df, n=14)
    if atr14 is None or atr14.dropna().empty:
        dm.note = "atr_na"
        return dm

    atr_val = atr14.dropna().iloc[-1]
    if pd.isna(atr_val) or float(atr_val) <= 0:
        dm.note = "atr_invalid"
        return dm

    dm.atr_pct = float(float(atr_val) / last_close)
    return dm