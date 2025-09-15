# portfolio/services/trend.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
import numpy as np
import pandas as pd
import yfinance as yf


# =========================
# ヘルパ
# =========================
def _normalize_ticker(raw: str) -> str:
    """
    入力を正規化。
    - 4～5桁の数字のみなら日本株とみなし「.T」を付与（例: '7203' -> '7203.T'）
    - それ以外はそのまま大文字化のみ
    """
    t = (raw or "").strip().upper()
    if not t:
        return t
    if "." in t:
        return t
    if t.isdigit() and len(t) in (4, 5):
        return f"{t}.T"
    return t


def _fetch_name_jp(ticker: str) -> str:
    """
    yfinance から銘柄名（日本語優先）を取得。
    取れなければティッカーでフォールバック。
    """
    try:
        info = getattr(yf.Ticker(ticker), "info", {}) or {}
        name = info.get("shortName") or info.get("longName") or info.get("name")
        if isinstance(name, str) and name.strip():
            return name.strip()
    except Exception:
        pass
    return ticker


# =========================
# 結果スキーマ
# =========================
@dataclass
class TrendResult:
    ticker: str
    name: str                   # 日本語名（無ければ英語/ティッカー）
    asof: str                   # 'YYYY-MM-DD'
    days: int                   # 直近使用日数
    signal: str                 # 'UP' | 'DOWN' | 'FLAT'
    reason: str
    slope: float                # 1日あたりの回帰傾き（終値）
    slope_annualized_pct: float # 年率換算(%)
    ma_short: Optional[float]   # 短期MAの最新値
    ma_long: Optional[float]    # 長期MAの最新値


# =========================
# メイン判定
# =========================
def detect_trend(
    ticker: str,
    days: int = 60,
    ma_short_win: int = 10,
    ma_long_win: int = 25,
) -> TrendResult:
    """
    直近 N 日の終値で線形回帰の傾きと移動平均を見て
    シンプルに UP/DOWN/FLAT を返す。
    """
    ticker = _normalize_ticker(ticker)
    if not ticker:
        raise ValueError("ticker is required")

    # 市場休場を考慮して余裕を持って period を長めに
    period_days = max(days + 30, 120)
    df = yf.download(ticker, period=f"{period_days}d", interval="1d", progress=False)
    if df is None or df.empty:
        raise ValueError("価格データを取得できませんでした")

    # 終値 Series
    s = df["Close"].dropna()
    if s.empty:
        raise ValueError("終値データが空でした")

    # 直近 'days' 営業日のみ
    s = s.tail(days)

    if len(s) < max(15, ma_long_win):
        raise ValueError(f"データ日数が不足しています（取得: {len(s)}日）")

    # --- 移動平均（必ず float/None に落とす）---
    ma_short_s = s.rolling(ma_short_win).mean()
    ma_long_s = s.rolling(ma_long_win).mean()

    ma_short_last: Optional[float] = None
    if not ma_short_s.empty:
        v = ma_short_s.iloc[-1]
        # v が 0次元 ndarray / numpy scalar / pandas scalar / Series(長さ1) でも安全に数値化
        try:
            val = getattr(v, "item", lambda: v)()
        except Exception:
            val = v
        if pd.notna(val):
            ma_short_last = float(val)

    ma_long_last: Optional[float] = None
    if not ma_long_s.empty:
        v = ma_long_s.iloc[-1]
        try:
            val = getattr(v, "item", lambda: v)()
        except Exception:
            val = v
        if pd.notna(val):
            ma_long_last = float(val)

    # 線形回帰（x は 0..n-1）
    y = np.asarray(s.values, dtype=float)
    x = np.arange(len(y), dtype=float)
    k, b = np.polyfit(x, y, 1)  # 傾き k

    # 年率換算の概算（営業日 ~ 252日）
    last_price = float(y[-1])
    slope_daily_pct = (k / last_price) * 100.0 if last_price else 0.0
    slope_ann_pct = slope_daily_pct * 252.0

    # シグナル判定（シンプル基準）
    signal = "FLAT"
    reason = "傾きが小さいため様子見"
    if slope_ann_pct >= 5.0:
        signal = "UP"
        reason = "回帰傾き(年率換算)が正で大きめ"
    elif slope_ann_pct <= -5.0:
        signal = "DOWN"
        reason = "回帰傾き(年率換算)が負で大きめ"

    # MA クロスで補強
    if ma_short_last is not None and ma_long_last is not None:
        if ma_short_last > ma_long_last and signal == "FLAT":
            signal, reason = "UP", "短期線が長期線を上回る(ゴールデンクロス気味)"
        elif ma_short_last < ma_long_last and signal == "FLAT":
            signal, reason = "DOWN", "短期線が長期線を下回る(デッドクロス気味)"

    asof = s.index[-1].date().isoformat()
    name = _fetch_name_jp(ticker)

    return TrendResult(
        ticker=ticker,
        name=name,
        asof=asof,
        days=int(len(s)),
        signal=signal,
        reason=reason,
        slope=float(k),
        slope_annualized_pct=float(slope_ann_pct),
        ma_short=ma_short_last,
        ma_long=ma_long_last,
    )