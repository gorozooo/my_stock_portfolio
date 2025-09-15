from __future__ import annotations
from dataclasses import dataclass
from typing import Optional
import re
import unicodedata
import numpy as np
import pandas as pd
import yfinance as yf

# 日本語名は tse.py に一本化！
from . import tse


# ------------------------------
# 文字列クリーニング
# ------------------------------
def _clean_text(s: str) -> str:
    if not isinstance(s, str):
        return s
    s = unicodedata.normalize("NFKC", s)
    s = re.sub(r"[\u200B-\u200D\uFEFF]", "", s)   # zero width & BOM
    s = re.sub(r"[\uFE00-\uFE0F]", "", s)         # variation selectors
    s = re.sub(r"[\u0000-\u001F\u007F]", "", s)   # control chars + DEL
    s = re.sub(r"[\uE000-\uF8FF]", "", s)         # PUA
    s = s.replace("\u3000", " ")
    s = re.sub(r"\s+", " ", s)
    return s.strip()


# ------------------------------
# ティッカー正規化
# ------------------------------
_JP_ALNUM = re.compile(r"^[0-9A-Z]{4,5}$")

def _normalize_ticker(raw: str) -> str:
    t = (raw or "").strip().upper()
    if not t:
        return t
    if "." in t:
        return t
    if _JP_ALNUM.match(t):
        return f"{t}.T"
    return t


# ------------------------------
# 日本語名の取得（tse に一本化）
# ------------------------------
def _fetch_name_prefer_jp(ticker: str) -> str:
    """
    1) tse.lookup_name_jp() で日本語名
    2) 見つからなければ（日本株コードなら）数値/英数字コード
    3) それ以外は yfinance 名（クリーニング）
    """
    head = (ticker or "").upper().split(".", 1)[0]

    # 1) TSE 辞書
    name = tse.lookup_name_jp(head)
    if isinstance(name, str) and name.strip():
        return _clean_text(name)

    # 2) 日本株コードなら英数字コードをそのまま
    if _JP_ALNUM.match(head):
        return head

    # 3) 海外などは yfinance
    try:
        info = getattr(yf.Ticker(ticker), "info", {}) or {}
        name = info.get("shortName") or info.get("longName") or info.get("name")
        if isinstance(name, str) and name.strip():
            return _clean_text(name)
    except Exception:
        pass

    return _clean_text(ticker)


# ------------------------------
# 結果スキーマ
# ------------------------------
@dataclass
class TrendResult:
    ticker: str
    name: str
    asof: str
    days: int
    signal: str         # 'UP' | 'DOWN' | 'FLAT'
    reason: str
    slope: float
    slope_annualized_pct: float
    ma_short: Optional[float]
    ma_long: Optional[float]


# ------------------------------
# ユーティリティ
# ------------------------------
def _to_float_or_none(v) -> Optional[float]:
    try:
        if isinstance(v, pd.Series):
            v = v.iloc[0]
        if pd.isna(v):
            return None
        return float(v)
    except Exception:
        return None


# ------------------------------
# メイン判定
# ------------------------------
def detect_trend(
    ticker: str,
    days: int = 60,
    ma_short_win: int = 10,
    ma_long_win: int = 30,
) -> TrendResult:
    ticker = _normalize_ticker(ticker)
    if not ticker:
        raise ValueError("ticker is required")

    # 休日を考慮して少し余裕を持って取得
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
    ma_long  = s.rolling(ma_long_win).mean()

    ma_s = _to_float_or_none(ma_short.iloc[[-1]])
    ma_l = _to_float_or_none(ma_long.iloc[[-1]])

    y = s.values.astype(float)
    x = np.arange(len(y), dtype=float)
    k, _b = np.polyfit(x, y, 1)

    last_price = y[-1]
    slope_daily_pct = (k / last_price) * 100.0 if last_price else 0.0
    slope_ann_pct = slope_daily_pct * 252.0

    signal = "FLAT"
    reason = "傾きが小さいため様子見"
    if slope_ann_pct >= 5.0:
        signal, reason = "UP", "回帰傾き(年率換算)が正で大きめ"
    elif slope_ann_pct <= -5.0:
        signal, reason = "DOWN", "回帰傾き(年率換算)が負で大きめ"

    if (ma_s is not None) and (ma_l is not None) and signal == "FLAT":
        if ma_s > ma_l:
            signal, reason = "UP", "短期線が長期線を上回る(ゴールデンクロス気味)"
        elif ma_s < ma_l:
            signal, reason = "DOWN", "短期線が長期線を下回る(デッドクロス気味)"

    asof = s.index[-1].date().isoformat()
    name = _fetch_name_prefer_jp(ticker)  # ← 常に tse を経由
    name = _clean_text(name)              # 念押しでクリーニング

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