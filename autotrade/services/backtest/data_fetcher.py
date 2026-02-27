"""
[FILE] autotrade/services/backtest/data_fetcher.py
[PATH] <project_root>/autotrade/services/backtest/data_fetcher.py

このファイルは何？
- yfinanceから価格データを取得する “データ取得専用” の部品です。
- 5分足（5m）と、日足（1d）を提供します。
"""

import pandas as pd
import yfinance as yf


def fetch_5m(ticker: str, prefer_period: str = "60d") -> pd.DataFrame:
    """
    5分足は取得制約があるので、まずは period で取得します。
    """
    df = yf.download(ticker, interval="5m", period=prefer_period, auto_adjust=False, progress=False)
    if df is None or df.empty:
        return pd.DataFrame()

    df = df.rename(columns={
        "Open": "open", "High": "high", "Low": "low", "Close": "close", "Volume": "volume"
    }).dropna()

    return df


def fetch_1d(ticker: str, prefer_period: str = "1y") -> pd.DataFrame:
    """
    日足（1d）。日足SMAフィルタで使う。
    """
    df = yf.download(ticker, interval="1d", period=prefer_period, auto_adjust=False, progress=False)
    if df is None or df.empty:
        return pd.DataFrame()

    df = df.rename(columns={
        "Open": "open", "High": "high", "Low": "low", "Close": "close", "Volume": "volume"
    }).dropna()

    return df