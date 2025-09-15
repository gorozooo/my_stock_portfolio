# v2: fix boolean check to avoid Series truthiness; always pass float/None to template
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional
import numpy as np
import pandas as pd
import yfinance as yf


# -------------------------
# 入力ティッカー正規化
# -------------------------
def _normalize_ticker(raw: str) -> str:
    """
    - 数字4桁(または5桁)だけなら日本株とみなし '.T' を付与（例: '7203' -> '7203.T'）
    - 既にサフィックスがある/海外株は大文字化のみ
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
    """yfinance から日本語名優先で銘柄名を取得（無ければティッカー）"""
    try:
        info = getattr(yf.Ticker(ticker), "info", {}) or {}
        name = info.get("shortName") or info.get("longName") or info.get("name")
        if isinstance(name, str) and name.strip():
            return name.strip()
    except Exception:
        pass
    return ticker


@dataclass
class TrendResult:
    ticker: str
    name: str
    asof: str
    days: int
    signal: str          # 'UP' | 'DOWN' | 'FLAT'
    reason: str
    slope: float         # 1日あたりの回帰傾き（終値）
    slope_annualized_pct: float
    ma_short: Optional[float]
    ma_long: Optional[float]


# -------------------------
# メイン判定
# -------------------------
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

    # 市場休日を考慮して多めに取得
    period_days = max(days + 30, 120)
    df = yf.download(ticker, period=f"{period_days}d", interval="1d", progress=False)
    if df is None or df.empty:
        raise ValueError("価格データを取得できませんでした")

    s = df["Close"].dropna()
    if s.empty:
        raise ValueError("終値データが空でした")

    # 直近 days 営業日（※ tail にキーワード引数は不可）
    s = s.tail(days)

    if len(s) < max(15, ma_long_win):
        raise ValueError(f"データ日数が不足しています（取得: {len(s)}日）")

    # --- 移動平均（必ず float/None に落とす）---
    ma_short_s = s.rolling(ma_short_win).mean()
    ma_long_s = s.rolling(ma_long_win).mean()

    ma_short_last = None
    if not ma_short_s.empty:
        v = ma_short_s.iloc[-1]
        if pd.notna(v):
            ma_short_last = float(v)

    ma_long_last = None
    if not ma_long_s.empty:
        v = ma_long_s.iloc[-1]
        if pd.notna(v):
            ma_long_last = float(v)

    # --- 線形回帰（y = kx + b の k）---
    y = s.values.astype(float)
    x = np.arange(len(y), dtype=float)
    k, _ = np.polyfit(x, y, 1)

    # 年率換算（営業日≈252日）
    last_price = float(y[-1])
    slope_daily_pct = (k / last_price) * 100.0 if last_price else 0.0
    slope_ann_pct = slope_daily_pct * 252.0

    # --- シグナル ---
    signal = "FLAT"
    reason = "傾きが小さいため様子見"
    if slope_ann_pct >= 5.0:
        signal, reason = "UP", "回帰傾き(年率換算)が正で大きめ"
    elif slope_ann_pct <= -5.0:
        signal, reason = "DOWN", "回帰傾き(年率換算)が負で大きめ"

    # MA クロスで補強（※ Series を if で判定しない！）
    if (ma_short_last is not None) and (ma_long_last is not None):
        if (ma_short_last > ma_long_last) and (signal == "FLAT"):
            signal, reason = "UP", "短期線が長期線を上回る(ゴールデンクロス気味)"
        elif (ma_short_last < ma_long_last) and (signal == "FLAT"):
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