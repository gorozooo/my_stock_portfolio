"""
[FILE] autotrade/services/backtest/data_fetcher.py
[PATH] <project_root>/autotrade/services/backtest/data_fetcher.py

このファイルは何？
- yfinanceから 5分足/日足データを取得する “データ取得専用” 部品です。

今回の変更：
- fetch_daily()（日足）を追加。
- 戦略エンジン側が「日足トレンド判定」などに使えるようにしました。

初心者ポイント：
- 取得処理をここに閉じ込めると、将来データソースを変えても影響が少ないです。
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

    df = df.rename(
        columns={"Open": "open", "High": "high", "Low": "low", "Close": "close", "Volume": "volume"}
    ).dropna()

    return df


def fetch_daily(ticker: str, prefer_period: str = "180d") -> pd.DataFrame:
    """
    日足（1d）を取得します。トレンド判定やフィルタに使います。

    返り値は index=datetime、列は open/high/low/close/volume に統一します。
    """
    df = yf.download(ticker, interval="1d", period=prefer_period, auto_adjust=False, progress=False)
    if df is None or df.empty:
        return pd.DataFrame()

    df = df.rename(
        columns={"Open": "open", "High": "high", "Low": "low", "Close": "close", "Volume": "volume"}
    ).dropna()

    return df