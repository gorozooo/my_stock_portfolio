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
- 壊れキャッシュ（列が無い/必須列不足など）を自動削除して再取得するようにしました。
- get_morning_stats(state=...) は universe の 5〜10銘柄から朝統計を作って返します。
- 集約方式（mean/median/wmean）を settings で切り替えできるようにしました（デフォ median）。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Optional, Dict, Any, List, Tuple

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


def _is_broken_cache(df: pd.DataFrame) -> bool:
    """
    壊れキャッシュ判定：
    - 行はあるのに列が無い（今回の再現）
    - 必須列が無い
    """
    if df is None:
        return True

    # 「行はあるのに列が0」は壊れ
    if len(df) > 0 and len(df.columns) == 0:
        return True

    required = {"open", "high", "low", "close"}

    # Pandas Index の truthiness 問題を避けて、必ず list 化する
    cols_list = list(df.columns)
    cols = set(str(c) for c in cols_list)

    if len(cols) == 0:
        return True

    if not required.issubset(cols):
        return True

    return False


def fetch_morning_5m(ticker: str, day: date, use_cache: bool = True) -> pd.DataFrame:
    """
    指定日の 9:00〜9:30 の5分足（最大7本くらい）を返す。
    """
    path = _cache_path(day, ticker)

    if use_cache:
        cached = _read_cache(path)
        if not cached.empty:
            # 壊れキャッシュは自動削除して再取得へ
            if _is_broken_cache(cached):
                try:
                    os.remove(path)
                except Exception:
                    pass
            else:
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

    keep = [c for c in ["open", "high", "low", "close", "volume"] if c in sliced.columns]
    sliced = sliced[keep].dropna()

    if not sliced.empty:
        _write_cache(path, sliced)

    return sliced


def compute_morning_metrics(ticker: str, day: date, use_cache: bool = True) -> MorningMetrics:
    """
    朝30分の指標
    - range_pct  : (high-low)/open
    - efficiency : |close-open|k/(high-low)
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


def _safe_float(x: Any, default: float = 0.0) -> float:
    try:
        if x is None:
            return float(default)
        return float(x)
    except Exception:
        return float(default)


def _median(xs: List[float]) -> float:
    if not xs:
        return 0.0
    ys = sorted(float(x) for x in xs)
    n = len(ys)
    m = n // 2
    if n % 2 == 1:
        return float(ys[m])
    return float((ys[m - 1] + ys[m]) / 2.0)


def _weighted_mean(xs: List[float], ws: List[float]) -> float:
    if not xs or not ws or len(xs) != len(ws):
        return 0.0
    # 0以下の重みは弾く
    pairs = [(float(x), float(w)) for x, w in zip(xs, ws) if float(w) > 0]
    if not pairs:
        return 0.0
    s_w = sum(w for _, w in pairs)
    if s_w <= 0:
        return 0.0
    return float(sum(x * w for x, w in pairs) / s_w)


def _pick_weights_from_universe(universe: Dict[str, Any], tickers: List[str]) -> List[float]:
    """
    universe.picks から avg_dv_yen を拾って重みにする（無ければ 1.0）
    """
    picks = universe.get("picks") or []
    dv_map: Dict[str, float] = {}
    for p in picks:
        if not isinstance(p, dict):
            continue
        t = p.get("ticker")
        if not t:
            continue
        dv = p.get("avg_dv_yen")
        try:
            dv_map[str(t)] = float(dv) if dv is not None else 1.0
        except Exception:
            dv_map[str(t)] = 1.0

    ws: List[float] = []
    for t in tickers:
        w = dv_map.get(str(t), 1.0)
        try:
            w = float(w)
        except Exception:
            w = 1.0
        # 極端な値でも壊れないように下限だけ置く
        ws.append(max(1.0, w))
    return ws


def get_morning_stats(*, state) -> Dict[str, Any]:
    """
    state（AutoTradeDailyState）から「朝30分の統計」を作って返す。

    集約方式（settingsで切替）:
      AUTOTRADE_MORNING_AGG = "median" (default) / "mean" / "wmean"
    """
    universe = getattr(state, "universe", None) or {}
    picks = universe.get("picks") or []
    tickers: List[str] = []
    for x in picks:
        if isinstance(x, dict) and x.get("ticker"):
            tickers.append(str(x["ticker"]))

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
        if df is None or df.empty:
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
        used.append(str(t))

    if not used:
        return {
            "range_pct": 0.0,
            "trend_pct": 0.0,
            "chop_ratio": 0.0,
            "tickers_used": [],
            "n_used": 0,
            "note": "no_morning_data",
        }

    agg = str(getattr(settings, "AUTOTRADE_MORNING_AGG", "median") or "median").lower().strip()

    if agg == "mean":
        range_val = float(sum(ranges) / max(1, len(ranges)))
        trend_val = float(sum(trends) / max(1, len(trends)))
        chop_val = float(sum(chops) / max(1, len(chops)))
        note = "ok_mean"

    elif agg == "wmean":
        ws = _pick_weights_from_universe(universe, used)
        range_val = _weighted_mean(ranges, ws)
        trend_val = _weighted_mean(trends, ws)
        chop_val = _weighted_mean(chops, ws)
        note = "ok_wmean"

    else:
        # default: median（外れ値に強い）
        range_val = _median(ranges)
        trend_val = _median(trends)
        chop_val = _median(chops)
        note = "ok_median"

    return {
        "range_pct": float(range_val),
        "trend_pct": float(trend_val),
        "chop_ratio": float(chop_val),
        "tickers_used": used,
        "n_used": int(len(used)),
        "note": note,
    }