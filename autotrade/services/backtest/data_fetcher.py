# ============================================================
# [FILE] data_fetcher.py
# [PATH] <project_root>/autotrade/services/backtest/data_fetcher.py
#
# このファイルは何？
# - yfinance から価格データを取得する「データ取得専用」部品です。
# - これまでの 5分足(fetch_5m) に加えて、日足(fetch_daily) を追加しました。
#
# 目的：
# - BREAKOUT などの戦略で「日足トレンド判定（例：20MA上 & 20MA上向き）」を入れられるようにするため。
# - 取得処理をここに閉じ込めることで、将来データソースを変えても影響を最小化できます。
# ============================================================

import pandas as pd
import yfinance as yf


def fetch_5m(ticker: str, prefer_period: str = "60d") -> pd.DataFrame:
    """
    5分足は取得制約があるので、まずは period で取得します。
    """
    df = yf.download(
        ticker,
        interval="5m",
        period=prefer_period,
        auto_adjust=False,
        progress=False,
    )
    if df is None or df.empty:
        return pd.DataFrame()

    df = (
        df.rename(
            columns={
                "Open": "open",
                "High": "high",
                "Low": "low",
                "Close": "close",
                "Volume": "volume",
            }
        )
        .dropna()
    )

    return df


def fetch_daily(ticker: str, period: str = "120d") -> pd.DataFrame:
    """
    日足を period で取得します（例：120d）。
    - 5分足だけだと「相場環境（トレンド日/レンジ日）」の判定ができないため追加。
    """
    df = yf.download(
        ticker,
        interval="1d",
        period=period,
        auto_adjust=False,
        progress=False,
    )
    if df is None or df.empty:
        return pd.DataFrame()

    df = (
        df.rename(
            columns={
                "Open": "open",
                "High": "high",
                "Low": "low",
                "Close": "close",
                "Volume": "volume",
            }
        )
        .dropna()
    )

    return df