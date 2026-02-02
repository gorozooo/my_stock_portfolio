"""
[FILE] autotrade/services/universe/metrics.py
[PATH] <project_root>/autotrade/services/universe/metrics.py

このファイルは何？
- 日足データから「流動性（売買代金）」と「動きやすさ（ATR%）」を計算する部品です。
- universe選定の“最初の関門”で、事故りやすい銘柄をここで落とします。

初心者向けの考え方：
- 売買代金が少ない → 約定しづらい → 触らない
- 動かなすぎ → チャンスが少ない → 触らない
- 動きすぎ → 事故りやすい → 触らない
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

import pandas as pd
import yfinance as yf
from django.conf import settings


# =====================
# データ構造（結果の箱）
# =====================
@dataclass
class DailyMetrics:
    ticker: str

    # 終値
    last_close: Optional[float] = None

    # 流動性：平均売買代金（円）
    avg_dv_yen: Optional[float] = None

    # 動きやすさ：ATR ÷ 終値（割合）
    atr_pct: Optional[float] = None

    # 参考情報
    avg_volume: Optional[float] = None
    note: str = ""   # 何かあったら理由を書く（debug用）


# =====================
# キャッシュ関連
# =====================
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


# =====================
# 日足取得
# =====================
def fetch_daily_1d(
    ticker: str,
    period: str = "180d",
    use_cache: bool = True,
) -> pd.DataFrame:
    """
    日足データを取得（キャッシュ優先）
    """
    path = _cache_path(ticker)

    if use_cache:
        cached = _read_cache(path)
        if not cached.empty:
            return cached

    df = yf.download(
        ticker,
        interval="1d",
        period=period,
        auto_adjust=False,
        progress=False,
    )

    if df is None or df.empty:
        return pd.DataFrame()

    df = df.rename(columns={
        "Open": "open",
        "High": "high",
        "Low": "low",
        "Close": "close",
        "Volume": "volume",
    })

    df = df[["open", "high", "low", "close", "volume"]].dropna()

    _write_cache(path, df)
    return df


# =====================
# ATR計算
# =====================
def _atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    """
    ATR（平均的な値動き幅）
    """
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        (df["high"] - df["low"]).abs(),
        (df["high"] - prev_close).abs(),
        (df["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)

    return tr.rolling(n).mean()


# =====================
# メイン：指標計算
# =====================
def compute_daily_metrics(
    ticker: str,
    lookback_days: int = 60,
    use_cache: bool = True,
) -> DailyMetrics:
    """
    日足から以下を計算：
    - avg_dv_yen : 平均売買代金（円）
    - atr_pct    : ATR ÷ 終値（割合）
    """

    dm = DailyMetrics(ticker=ticker)

    df = fetch_daily_1d(ticker, use_cache=use_cache)

    if df.empty:
        dm.note = "no_data"
        return dm

    # --- 数値を必ず数値に直す（ここが超重要） ---
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.dropna()
    df = df.tail(max(lookback_days, 30))

    if len(df) < 20:
        dm.note = "too_short"
        return dm

    last_close = df["close"].iloc[-1]
    if pd.isna(last_close) or float(last_close) <= 0:
        dm.note = "bad_close"
        return dm

    last_close = float(last_close)
    dm.last_close = last_close

    # --- 売買代金（流動性） ---
    dv = df["close"] * df["volume"]
    dv = dv.dropna()

    if dv.empty:
        dm.note = "dv_na"
        return dm

    dm.avg_dv_yen = float(dv.mean())
    dm.avg_volume = float(df["volume"].mean())

    # --- ATR%（動きやすさ） ---
    atr14 = _atr(df, n=14)
    atr14 = atr14.dropna()

    if atr14.empty:
        dm.note = "atr_na"
        return dm

    atr_val = float(atr14.iloc[-1])
    dm.atr_pct = atr_val / last_close if last_close > 0 else None

    return dm