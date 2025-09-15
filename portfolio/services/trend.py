# portfolio/services/trend.py
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, Dict
import os
import unicodedata

import numpy as np
import pandas as pd
import yfinance as yf


# ============== 文字クレンジング ==============
def _clean_text(s: Optional[str]) -> str:
    if s is None:
        return ""
    s = str(s)
    s = unicodedata.normalize("NFKC", s)
    out = []
    for ch in s:
        cat = unicodedata.category(ch)
        if cat[0] == "C":  # 制御/書式/私用領域など
            continue
        if ch in "\u200B\u200C\u200D\u2060\uFEFF":
            continue
        out.append(ch)
    return "".join(out).strip()


# ============== 日本語銘柄名 CSV ローダ ==============
_TSE_CSV_PATH = os.environ.get(
    "TSE_CSV_PATH",
    os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "tse_list.csv"),
)

_TSE_MAP: Dict[str, str] = {}
_TSE_CSV_MTIME: float = 0.0


def _load_tse_map_if_needed() -> None:
    global _TSE_MAP, _TSE_CSV_MTIME
    if not os.path.isfile(_TSE_CSV_PATH):
        return

    mtime = os.path.getmtime(_TSE_CSV_PATH)
    if _TSE_MAP and _TSE_CSV_MTIME == mtime:
        return

    try:
        df = pd.read_csv(_TSE_CSV_PATH, dtype=str, encoding="utf-8-sig")
    except Exception:
        _TSE_MAP, _TSE_CSV_MTIME = {}, 0.0
        return

    # 列名正規化
    cols = {_clean_text(c).lower(): c for c in df.columns}
    code_col = cols.get("code") or cols.get("ｺｰﾄﾞ") or cols.get("コード")
    name_col = cols.get("name") or cols.get("銘柄名") or cols.get("めいがらめい")
    if not code_col or not name_col:
        _TSE_MAP, _TSE_CSV_MTIME = {}, 0.0
        return

    df[code_col] = df[code_col].map(_clean_text)
    df[name_col] = df[name_col].map(_clean_text)
    df = df[(df[code_col].str.fullmatch(r"\d{4,5}")) & (df[name_col] != "")]
    _TSE_MAP = dict(zip(df[code_col], df[name_col]))
    _TSE_CSV_MTIME = mtime


def _lookup_name_jp_from_csv(ticker: str) -> Optional[str]:
    _load_tse_map_if_needed()
    if not _TSE_MAP:
        return None
    t = (ticker or "").upper().strip()
    if not t:
        return None
    numeric = t.split(".", 1)[0]
    if numeric.isdigit() and len(numeric) in (4, 5):
        return _TSE_MAP.get(numeric)
    return None


# ============== ティッカー正規化 / 名前取得 ==============
def _normalize_ticker(raw: str) -> str:
    t = (raw or "").strip().upper()
    if not t:
        return t
    if "." in t:
        return t
    if t.isdigit() and len(t) in (4, 5):
        return f"{t}.T"
    return t


def _fetch_name_prefer_jp(ticker: str) -> str:
    name_csv = _lookup_name_jp_from_csv(ticker)
    if isinstance(name_csv, str) and name_csv.strip():
        return name_csv.strip()

    try:
        info = getattr(yf.Ticker(ticker), "info", {}) or {}
        name = info.get("shortName") or info.get("longName") or info.get("name")
        if isinstance(name, str) and name.strip():
            return _clean_text(name)
    except Exception:
        pass
    return ticker


# ============== 結果スキーマ ==============
@dataclass
class TrendResult:
    ticker: str
    name: str
    asof: str
    days: int
    signal: str
    reason: str
    slope: float
    slope_annualized_pct: float
    ma_short: Optional[float]
    ma_long: Optional[float]


# ============== メイン判定 ==============
def detect_trend(
    ticker: str,
    days: int = 60,
    ma_short_win: int = 10,
    ma_long_win: int = 30,
) -> TrendResult:
    ticker = _normalize_ticker(ticker)
    if not ticker:
        raise ValueError("ticker is required")

    period_days = max(days + 30, 120)
    df = yf.download(ticker, period=f"{period_days}d", interval="1d", progress=False)
    if df is None or df.empty:
        raise ValueError("価格データを取得できませんでした")

    s = df["Close"].dropna()
    if s.empty:
        raise ValueError("終値データが空でした")

    s = s.tail(days)
    if len(s) < max(15, ma_long_win):
        raise ValueError(f"データ日数が不足しています（取得: {len(s)}日）")

    ma_short = s.rolling(ma_short_win).mean()
    ma_long = s.rolling(ma_long_win).mean()

    def _to_float_or_none(v) -> Optional[float]:
        try:
            if pd.isna(v):
                return None
        except Exception:
            pass
        try:
            return float(v)
        except Exception:
            return None

    ma_s = _to_float_or_none(ma_short.iloc[-1])
    ma_l = _to_float_or_none(ma_long.iloc[-1])

    y = s.values.astype(float)
    x = np.arange(len(y), dtype=float)
    k, _ = np.polyfit(x, y, 1)

    last_price = y[-1]
    slope_daily_pct = (k / last_price) * 100.0 if last_price else 0.0
    slope_ann_pct = slope_daily_pct * 252.0

    signal = "FLAT"
    reason = "傾きが小さいため様子見"
    if slope_ann_pct >= 5.0:
        signal, reason = "UP", "回帰傾き(年率換算)が正で大きめ"
    elif slope_ann_pct <= -5.0:
        signal, reason = "DOWN", "回帰傾き(年率換算)が負で大きめ"

    if (ma_s is not None) and (ma_l is not None):
        if ma_s > ma_l and signal == "FLAT":
            signal, reason = "UP", "短期線が長期線を上回る(ゴールデンクロス気味)"
        elif ma_s < ma_l and signal == "FLAT":
            signal, reason = "DOWN", "短期線が長期線を下回る(デッドクロス気味)"

    asof = s.index[-1].date().isoformat()
    name = _clean_text(_fetch_name_prefer_jp(ticker))

    return TrendResult(
        ticker=ticker,
        name=name,
        asof=asof,
        days=int(len(s)),
        signal=signal,
        reason=reason,
        slope=float(k),
        slope_annualized_pct=float(slope_ann_pct),
        ma_short=ma_s,
        ma_long=ma_l,
    )