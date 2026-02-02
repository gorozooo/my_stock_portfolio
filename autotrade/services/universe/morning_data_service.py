"""
[FILE] autotrade/services/universe/morning_data_service.py
[PATH] <project_root>/autotrade/services/universe/morning_data_service.py

このファイルは何？
- 「朝30分（9:00〜9:30）の5分足」を取得してキャッシュし、
  朝指標（朝レンジ%・効率）を計算する専用サービスです。

なぜ分ける？
- 5分足取得は重い＆制限があるので、ここに閉じ込めてキャッシュ運用を徹底します。

キャッシュの置き場所
- media/autotrade/cache/morning_5m/YYYYMMDD/<ticker>.csv

初心者ポイント
- いったんキャッシュが作られれば、同じ日の朝データは “読み出すだけ” で速くなります。

追加したこと（今回の変更）
- get_morning_stats(state=...) を追加しました。
  - state.universe に入っている 5〜10銘柄を使って、
    朝の統計（range_pct / trend_pct / chop_ratio）を作って返します。

今回のバグ修正（重要）
- yfinance が 5分足で MultiIndex（例: ('Open','7011.T')）を返すことがあるため、
  _normalize_cols() で列名を単層化してから正規化するようにしました。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Optional, Dict, Any, List

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
    """
    yfinance の返す列を以下に統一する:
      open, high, low, close, volume

    注意:
    - yfinance は状況によって MultiIndex列（例: ('Open','7011.T')）になる。
      その場合は “先頭レベル(Open/High/...)” を使って単層化する。
    """
    if df is None or df.empty:
        return df

    df = df.copy()

    # --- MultiIndex を単層化（最重要） ---
    try:
        if isinstance(df.columns, pd.MultiIndex):
            # 例: ('Open','7011.T') -> 'Open'
            df.columns = [c[0] if isinstance(c, tuple) and len(c) > 0 else c for c in df.columns]
        else:
            # tuple列名が混ざるケース保険
            df.columns = [c[0] if isinstance(c, tuple) and len(c) > 0 else c for c in df.columns]
    except Exception:
        # 失敗しても落ちない（そのまま続行）
        pass

    rename: Dict[Any, str] = {}
    for k in df.columns:
        lk = str(k).strip().lower()
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


def _coerce_numeric_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    """
    どこかで文字列になっても落ちないように、必ず数値化しておく。
    """
    if df is None or df.empty:
        return df
    df = df.copy()
    for c in ["open", "high", "low", "close", "volume"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna()
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
        df = _normalize_cols(df)
        df = _coerce_numeric_ohlcv(df)
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
    指定日の 9:00〜9:30 の5分足（最大6〜7本）を返す。

    重要：
    - タイムゾーンや足のラベルで slice が空になりやすいので、
      「JSTに寄せる → その日付だけに絞る → between_time」で安全に抜く。
    """
    path = _cache_path(day, ticker)
    if use_cache:
        cached = _read_cache(path)
        if not cached.empty and len(cached.columns) > 0:
            return cached

    df = yf.download(ticker, interval="5m", period="5d", auto_adjust=False, progress=False)
    if df is None or df.empty:
        return pd.DataFrame()

    df = _normalize_cols(df)
    df = _to_jst_index(df)
    df = _coerce_numeric_ohlcv(df)

    if df is None or df.empty:
        return pd.DataFrame()

    # まず「その日付」だけに絞る（JSTで判定）
    try:
        df_day = df[df.index.date == day].copy()
    except Exception:
        df_day = df.copy()

    if df_day is None or df_day.empty:
        return pd.DataFrame()

    # 次に 09:00〜09:30 を抜く（between_time は安定）
    try:
        sliced = df_day.between_time("09:00", "09:30").copy()
    except Exception:
        start_dt = pd.Timestamp(datetime.combine(day, time(9, 0)), tz=JST)
        end_dt = pd.Timestamp(datetime.combine(day, time(9, 30)), tz=JST)
        sliced = df_day[(df_day.index >= start_dt) & (df_day.index <= end_dt)].copy()

    keep = [c for c in ["open", "high", "low", "close", "volume"] if c in sliced.columns]
    sliced = sliced[keep].dropna()
    sliced = _coerce_numeric_ohlcv(sliced)

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

    if df is None or df.empty or len(df.columns) == 0:
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


def _safe_float(x: Any, default: float = 0.0) -> float:
    try:
        if x is None:
            return float(default)
        return float(x)
    except Exception:
        return float(default)


def get_morning_stats(*, state) -> Dict[str, Any]:
    """
    state（AutoTradeDailyState）から「朝30分の統計」を作って返す。

    返す値（decide_strategy が期待する形）
      {
        "range_pct": 0.015,     # 値幅（1.5%）
        "trend_pct": 0.010,     # 始値→終値の方向（+1.0% など）
        "chop_ratio": 0.60,     # 行ったり来たり度（0〜1、1ほど往復）
        "tickers_used": [...],
        "n_used": 6
      }
    """
    universe = getattr(state, "universe", None) or {}
    picks = universe.get("picks") or []

    tickers: List[str] = []
    for x in picks:
        if isinstance(x, dict) and x.get("ticker"):
            tickers.append(str(x["ticker"]).strip())
        elif isinstance(x, str) and x.strip():
            tickers.append(x.strip())

    if not tickers:
        return {
            "range_pct": 0.0,
            "trend_pct": 0.0,
            "chop_ratio": 0.0,
            "tickers_used": [],
            "n_used": 0,
            "note": "no_picks",
        }

    day = getattr(state, "date", None) or date.today()

    ranges: List[float] = []
    trends: List[float] = []
    chops: List[float] = []
    used: List[str] = []

    for t in tickers:
        df = fetch_morning_5m(t, day, use_cache=True)
        if df is None or df.empty or len(df.columns) == 0:
            continue
        if not all(c in df.columns for c in ["open", "high", "low", "close"]):
            continue

        o = _safe_float(df["open"].iloc[0], default=0.0)
        if o <= 0:
            continue

        h = _safe_float(df["high"].max(), default=o)
        l = _safe_float(df["low"].min(), default=o)
        c = _safe_float(df["close"].iloc[-1], default=o)

        rng = max(h - l, 0.0)
        range_pct = (rng / o) if o > 0 else 0.0
        trend_pct = ((c - o) / o) if o > 0 else 0.0

        # efficiency = |close-open|/(high-low)
        if rng <= 0:
            efficiency = 0.0
        else:
            efficiency = abs(c - o) / rng

        # chop_ratio: 1に近いほど“往復が多い”
        chop_ratio = 1.0 - float(max(0.0, min(1.0, efficiency)))

        ranges.append(float(range_pct))
        trends.append(float(trend_pct))
        chops.append(float(chop_ratio))
        used.append(t)

    if not used:
        return {
            "range_pct": 0.0,
            "trend_pct": 0.0,
            "chop_ratio": 0.0,
            "tickers_used": [],
            "n_used": 0,
            "note": "no_morning_data",
        }

    range_mean = float(sum(ranges) / max(1, len(ranges)))
    trend_mean = float(sum(trends) / max(1, len(trends)))
    chop_mean = float(sum(chops) / max(1, len(chops)))

    return {
        "range_pct": range_mean,
        "trend_pct": trend_mean,
        "chop_ratio": chop_mean,
        "tickers_used": used,
        "n_used": int(len(used)),
        "note": "ok",
    }