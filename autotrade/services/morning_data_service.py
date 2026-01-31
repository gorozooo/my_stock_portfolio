"""
[FILE] autotrade/services/morning_data_service.py
[PATH] <project_root>/autotrade/services/morning_data_service.py

このファイルは何？
- 「朝30分（9:00〜9:30）の5分足」を取得してキャッシュし、
  朝指標（朝レンジ%・効率）を計算する専用サービスです。

なぜ分ける？
- 5分足取得は重い＆制限があるので、ここに閉じ込めてキャッシュ運用を徹底します。

キャッシュの置き場所
- media/autotrade/cache/morning_5m/YYYYMMDD/<ticker>.csv

初心者ポイント
- いったんキャッシュが作られれば、同じ日の朝データは “読み出すだけ” で速くなります。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Optional, Tuple

import pandas as pd
import yfinance as yf
from django.conf import settings


JST = "Asia/Tokyo"


@dataclass
class MorningMetrics:
    ticker: str
    day: date
    range_pct: Optional[float] = None     # (high-low)/open
    efficiency: Optional[float] = None    # |close-open|/(high-low)
    bars: int = 0
    note: str = ""


def _cache_dir_for(day: date) -> str:
    base = getattr(settings, "MEDIA_ROOT", "media")
    d = os.path.join(base, "autotrade", "cache", "morning_5m", day.strftime("%Y%m%d"))
    os.makedirs(d, exist_ok=True)
    return d


def _cache_path(day: date, ticker: str) -> str:
    safe = ticker.replace("/", "_")
    return os.path.join(_cache_dir_for(day), f"{safe}.csv")


def _to_jst_index(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    idx = df.index
    # yfinance の index は tz-aware のことが多い。なければ UTC とみなして JST に寄せる。
    if getattr(idx, "tz", None) is None:
        df = df.copy()
        df.index = df.index.tz_localize("UTC").tz_convert(JST)
    else:
        df = df.copy()
        df.index = df.index.tz_convert(JST)
    return df


def _normalize_cols(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    rename = {}
    for k in df.columns:
        lk = str(k).lower()
        if lk == "open":
            rename[k] = "open"
        elif lk == "high":
            rename[k] = "high"
        elif lk == "low":
            rename[k] = "low"
        elif lk == "close":
            rename[k] = "close"
        elif lk == "volume":
            rename[k] = "volume"
    df = df.rename(columns=rename)
    return df


def _read_cache(path: str) -> pd.DataFrame:
    if not os.path.exists(path):
        return pd.DataFrame()
    try:
        df = pd.read_csv(path)
        if df.empty:
            return df
        # 保存時に index を "dt" 列にしている
        if "dt" in df.columns:
            df["dt"] = pd.to_datetime(df["dt"])
            df = df.set_index("dt")
            # dt は JST として保存する
            if getattr(df.index, "tz", None) is None:
                df.index = df.index.tz_localize(JST)
        return df
    except Exception:
        return pd.DataFrame()


def _write_cache(path: str, df: pd.DataFrame) -> None:
    if df is None or df.empty:
        return
    out = df.copy()
    out = out.reset_index().rename(columns={"index": "dt"})
    # index名が入ってる場合
    if "Datetime" in out.columns:
        out = out.rename(columns={"Datetime": "dt"})
    if "dt" not in out.columns:
        out.insert(0, "dt", df.index.astype(str))
    out.to_csv(path, index=False, encoding="utf-8")


def fetch_morning_5m(ticker: str, day: date, use_cache: bool = True) -> pd.DataFrame:
    """
    指定日の 9:00〜9:30 の5分足（最大6本）を返す。
    """
    path = _cache_path(day, ticker)
    if use_cache:
        cached = _read_cache(path)
        if not cached.empty:
            return cached

    # yfinance: 5分足は period 制約があるため、数日分だけ取って JST に変換して切り出す
    df = yf.download(ticker, interval="5m", period="5d", auto_adjust=False, progress=False)
    if df is None or df.empty:
        return pd.DataFrame()

    df = _normalize_cols(df)
    df = _to_jst_index(df)

    start_dt = datetime.combine(day, time(9, 0))
    end_dt = datetime.combine(day, time(9, 30))

    # indexは tz-aware(JST) なので tz を付ける
    start_dt = pd.Timestamp(start_dt, tz=JST)
    end_dt = pd.Timestamp(end_dt, tz=JST)

    sliced = df[(df.index >= start_dt) & (df.index <= end_dt)].copy()
    # 必須列だけ
    keep = [c for c in ["open", "high", "low", "close", "volume"] if c in sliced.columns]
    sliced = sliced[keep].dropna()

    if not sliced.empty:
        _write_cache(path, sliced)

    return sliced


def compute_morning_metrics(ticker: str, day: date, use_cache: bool = True) -> MorningMetrics:
    """
    朝30分の指標
    - range_pct  : (high-low)/open
    - efficiency : |close-open|/(high-low)
    """
    df = fetch_morning_5m(ticker, day, use_cache=use_cache)
    mm = MorningMetrics(ticker=ticker, day=day, bars=0)

    if df is None or df.empty:
        mm.note = "no_data"
        return mm

    if not all(x in df.columns for x in ["open", "high", "low", "close"]):
        mm.note = "missing_cols"
        return mm

    mm.bars = int(len(df))

    o = float(df["open"].iloc[0])
    h = float(df["high"].max())
    l = float(df["low"].min())
    c = float(df["close"].iloc[-1])

    if o <= 0:
        mm.note = "bad_open"
        return mm

    rng = max(h - l, 0.0)
    mm.range_pct = float(rng / o) if rng >= 0 else None

    if rng <= 0:
        mm.efficiency = 0.0
    else:
        mm.efficiency = float(abs(c - o) / rng)

    return mm