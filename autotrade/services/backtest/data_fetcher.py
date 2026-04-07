"""
[FILE] autotrade/services/backtest/data_fetcher.py
[PATH] <project_root>/autotrade/services/backtest/data_fetcher.py

このファイルは何？
- yfinanceから価格データを取得する “データ取得専用” の部品です。
- 5分足（5m）と、日足（1d）を提供します。

今回の修正：
- 1銘柄timeoutや列崩れでも例外を上に投げず、安全に空DataFrameを返す
- yfinanceのMultiIndex列にも対応
- open/high/low/close/volume を必ず同じ形に正規化する
"""

from __future__ import annotations

from typing import Dict, Any

import pandas as pd
import yfinance as yf


_REQUIRED_PRICE_COLS = ["open", "high", "low", "close", "volume"]


def _empty_df() -> pd.DataFrame:
    return pd.DataFrame(columns=_REQUIRED_PRICE_COLS)


def _normalize_ohlcv_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    yfinance の返り値を
    open / high / low / close / volume
    に正規化する。

    - 単純カラムにも対応
    - MultiIndexにも対応
    """
    if df is None or df.empty:
        return _empty_df()

    # -----------------------------------------------------
    # MultiIndex（例: ('Close', '7203.T')）に対応
    # -----------------------------------------------------
    if isinstance(df.columns, pd.MultiIndex):
        picked: Dict[str, Any] = {}

        for col in df.columns:
            labels = [str(x).strip().lower() for x in col if str(x).strip()]
            for target in ["open", "high", "low", "close", "volume"]:
                if target in labels and target not in picked:
                    picked[target] = df[col]

        if not picked:
            return _empty_df()

        out = pd.DataFrame(index=df.index)
        for c in _REQUIRED_PRICE_COLS:
            if c in picked:
                out[c] = picked[c]

        df = out

    # -----------------------------------------------------
    # 通常カラム対応
    # -----------------------------------------------------
    else:
        rename_map = {}
        for c in df.columns:
            c0 = str(c).strip().lower()
            if c0 == "open":
                rename_map[c] = "open"
            elif c0 == "high":
                rename_map[c] = "high"
            elif c0 == "low":
                rename_map[c] = "low"
            elif c0 == "close":
                rename_map[c] = "close"
            elif c0 == "volume":
                rename_map[c] = "volume"

        if not rename_map:
            return _empty_df()

        df = df.rename(columns=rename_map)

    # 必要列だけ残す
    out = pd.DataFrame(index=df.index)
    for c in _REQUIRED_PRICE_COLS:
        if c in df.columns:
            out[c] = df[c]

    # volume が無いケースは 0 埋め
    if "volume" not in out.columns:
        out["volume"] = 0

    # open/high/low/close が欠けたら使えない
    for c in ["open", "high", "low", "close"]:
        if c not in out.columns:
            return _empty_df()

    # 数値化
    for c in _REQUIRED_PRICE_COLS:
        out[c] = pd.to_numeric(out[c], errors="coerce")

    # OHLC が欠けている行は落とす
    out = out.dropna(subset=["open", "high", "low", "close"]).copy()

    if out.empty:
        return _empty_df()

    return out


def _download(
    *,
    ticker: str,
    interval: str,
    period: str,
) -> pd.DataFrame:
    """
    yfinance download を安全実行する。
    失敗時は例外を上げず、空DataFrameを返す。
    """
    timeout_sec = float(10.0)

    try:
        df = yf.download(
            ticker,
            interval=interval,
            period=period,
            auto_adjust=False,
            progress=False,
            threads=False,
            timeout=timeout_sec,
        )
    except Exception:
        return _empty_df()

    if df is None or getattr(df, "empty", True):
        return _empty_df()

    try:
        df = _normalize_ohlcv_columns(df)
    except Exception:
        return _empty_df()

    if df is None or df.empty:
        return _empty_df()

    return df


def fetch_5m(ticker: str, prefer_period: str = "60d") -> pd.DataFrame:
    """
    5分足は取得制約があるので、まずは period で取得します。
    """
    return _download(ticker=ticker, interval="5m", period=prefer_period)


def fetch_1d(ticker: str, prefer_period: str = "1y") -> pd.DataFrame:
    """
    日足（1d）。日足SMAフィルタで使う。
    """
    return _download(ticker=ticker, interval="1d", period=prefer_period)