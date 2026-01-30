"""
[FILE] autotrade/services/backtest/data_fetcher.py
[PATH] <project_root>/autotrade/services/backtest/data_fetcher.py

このファイルは何？
- yfinanceから 5分足データを取得する “データ取得専用” の部品です。

初心者ポイント：
- 取得処理はここに閉じ込めると、将来データソースを変えても影響が少ないです。
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