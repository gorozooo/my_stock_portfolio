# portfolio/services/trend.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd
import yfinance as yf


@dataclass
class TrendResult:
    ticker: str
    asof: str                 # ISO 文字列（最新営業日）
    days: int                 # 解析対象日数（実際に残った営業日数）
    signal: str               # "UP" | "DOWN" | "FLAT"
    reason: str               # 人間向け説明
    slope: float              # 1日あたりの価格傾き（通貨単位）
    slope_annualized_pct: float  # 年換算(252営業日)の平均比[%]
    ma_short: Optional[float] # 短期移動平均の最新値
    ma_long: Optional[float]  # 長期移動平均の最新値


def _load_ohlc(ticker: str, need_days: int) -> pd.DataFrame:
    """
    yfinanceから日足を取得。
    将来の欠損を考慮し、必要日数の3倍（最低120日）を取得してから日付で絞り込む。
    """
    lookback_days = max(need_days * 3, 120)
    df = yf.download(
        ticker,
        period=f"{lookback_days}d",
        interval="1d",
        auto_adjust=True,
        progress=False,
        threads=False,
    )

    if df is None or df.empty:
        raise ValueError(f"no data for {ticker}")

    # index をタイムゾーンなしの DatetimeIndex に揃える
    df.index = pd.to_datetime(df.index).tz_localize(None)

    # “終値”がない銘柄はあり得ないが、念のためガード
    if "Close" not in df.columns:
        raise ValueError("unexpected data shape: column 'Close' not found")

    # 祝日等で穴が空くことがあるため、営業日Bでリサンプル（前日埋め）
    # これにより rolling の窓が安定する
    df = (
        df[["Close"]]
        .resample("B")
        .last()
        .ffill()
    )
    return df


def _slice_by_days(df: pd.DataFrame, days: int) -> pd.DataFrame:
    """今日からdays日前以降に日付フィルタ。"""
    cutoff = pd.Timestamp.today().normalize() - pd.Timedelta(days=days)
    df2 = df[df.index >= cutoff]
    return df2


def _slope_and_ma(close: pd.Series, ma_short: int, ma_long: int):
    """
    価格系列に対して線形回帰で日次傾きを算出。
    さらに短期/長期の単純移動平均（SMA）を計算。
    """
    n = len(close)
    x = np.arange(n, dtype=float)
    y = close.astype(float).to_numpy()

    # polyfit(1次)で傾き（日次）と切片
    k, b = np.polyfit(x, y, 1)

    mean_price = float(np.mean(y))
    # 平均価格に対する日次傾き → 年換算(252営業日)で%表示
    slope_annualized_pct = float((k / mean_price) * 252 * 100) if mean_price else 0.0

    s_ma = close.rolling(window=ma_short, min_periods=max(1, ma_short // 2)).mean()
    l_ma = close.rolling(window=ma_long,  min_periods=max(1, ma_long  // 2)).mean()

    return float(k), float(slope_annualized_pct), float(s_ma.iloc[-1]), float(l_ma.iloc[-1])


def detect_trend(
    ticker: str,
    days: int = 60,          # 直近60日で判定（約3か月弱）
    ma_short: int = 10,      # 10日移動平均
    ma_long: int = 25,       # 25日移動平均
) -> TrendResult:
    """
    直近days“日”の終値でトレンドを判定。
    - 日付でフィルタ（tail(n)は使わない）
    - 線形回帰の傾き + 短期/長期MAの位置関係で UP/DOWN/FLAT
    """
    if days < 15:
        # あまり短いとノイズが強いので下限ガード
        days = 15

    df = _load_ohlc(ticker, need_days=days)
    df = _slice_by_days(df, days=days)

    # 安全装置：行数が少なすぎる場合はエラー
    if len(df) < max(ma_long + 3, 15):
        raise ValueError(
            f"too few rows ({len(df)}) in last {days} days for {ticker}"
        )

    close = df["Close"]
    k, slope_ann_pct, ma_s, ma_l = _slope_and_ma(close, ma_short, ma_long)

    # ルール：MAの位置関係と傾きの符号で最終判定
    if ma_s > ma_l and k > 0:
        signal = "UP"
        reason = f"短期MA({ma_short})が長期MA({ma_long})より上、かつ回帰傾きが正"
    elif ma_s < ma_l and k < 0:
        signal = "DOWN"
        reason = f"短期MA({ma_short})が長期MA({ma_long})より下、かつ回帰傾きが負"
    else:
        signal = "FLAT"
        reason = "MA位置と回帰傾きが一致せず、明確な方向性なし"

    asof = df.index.max().strftime("%Y-%m-%d")
    return TrendResult(
        ticker=ticker,
        asof=asof,
        days=len(df),
        signal=signal,
        reason=reason,
        slope=float(k),
        slope_annualized_pct=float(slope_ann_pct),
        ma_short=float(ma_s),
        ma_long=float(ma_l),
    )