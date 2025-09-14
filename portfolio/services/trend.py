# portfolio/services/trend.py
from __future__ import annotations
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import yfinance as yf
from sklearn.linear_model import LinearRegression

TOKYO = timezone(timedelta(hours=9))

@dataclass
class TrendResult:
    ticker: str
    asof: str
    days: int
    slope: float
    slope_annualized_pct: float
    ma_short: float
    ma_long: float
    signal: str   # "up" | "down" | "flat"
    reason: str

def _linear_regression_slope(series: pd.Series) -> float:
    """日付を 0..N-1 に置いて線形回帰の傾きを返す（終値ベース）"""
    y = series.values.reshape(-1, 1)
    x = np.arange(len(series)).reshape(-1, 1)
    model = LinearRegression()
    model.fit(x, y)
    return float(model.coef_[0][0])

def detect_trend(ticker: str, days: int = 90) -> TrendResult:
    end = datetime.now(TOKYO)
    start = end - timedelta(days=days + 10)  # 余裕をもって取得
    df = yf.download(ticker, start=start.date(), end=end.date(), progress=False, auto_adjust=True)
    if df.empty:
        raise ValueError(f"データ取得に失敗: {ticker}")

    close = df["Close"].dropna()
    if len(close) < 30:
        raise ValueError(f"データが少なすぎます({len(close)}日)。別のティッカーを試してください。")

    # 移動平均
    ma_short = close.rolling(10).mean().iloc[-1]
    ma_long  = close.rolling(30).mean().iloc[-1]

    # 傾き（1日あたりの価格変化量）/ 年率換算パーセンテージ
    slope = _linear_regression_slope(close.tail(days=min(60, len(close))))
    last = close.iloc[-1]
    slope_annualized_pct = 0.0
    if last > 0:
        slope_annualized_pct = (slope / last) * 252 * 100  # 252 営業日を年換算の目安に

    # ルールベース信号
    # 1) 短期MA > 長期MA かつ 傾きプラス → up
    # 2) 短期MA < 長期MA かつ 傾きマイナス → down
    # 3) それ以外 → flat
    signal = "flat"
    reasons = []
    if ma_short > ma_long:
        reasons.append("短期MAが長期MAを上回り")
    elif ma_short < ma_long:
        reasons.append("短期MAが長期MAを下回り")
    if slope > 0:
        reasons.append("回帰傾きが正")
    elif slope < 0:
        reasons.append("回帰傾きが負")

    if (ma_short > ma_long) and (slope > 0):
        signal = "up"
    elif (ma_short < ma_long) and (slope < 0):
        signal = "down"

    reason = "、".join(reasons) if reasons else "明確な優位性なし"
    return TrendResult(
        ticker=ticker.upper(),
        asof=end.strftime("%Y-%m-%d %H:%M"),
        days=days,
        slope=slope,
        slope_annualized_pct=round(slope_annualized_pct, 2),
        ma_short=round(float(ma_short), 3),
        ma_long=round(float(ma_long), 3),
        signal=signal,
        reason=reason,
    )
