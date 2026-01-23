# -*- coding: utf-8 -*-
"""
ファイル: aiapp/services/daytrade/bars_5m_daytrade.py

これは何？
- デイトレ専用の「指定銘柄・指定日」の5分足取得＆キャッシュ。
- 戻り値は daytrade の backtest / signal で使いやすい形（dt + vwap 付き）。

仕様
- yfinance で interval="5m" を取得
- JSTへ統一
- その日（trade_date）だけに絞る
- vwap（累積近似）を生成
- media/aiapp/daytrade/bars_5m/<code>/YYYYMMDD.parquet に保存

注意
- 無料データは欠損・遅延があり得るので、空なら空で返す（安全側）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date as _date, datetime as _dt, time as _time, timedelta as _td
from pathlib import Path
from typing import Optional

import pandas as pd
import yfinance as yf
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)

# デイトレ専用 5分足キャッシュ
DAYTRADE_BARS_5M_DIR = Path(settings.MEDIA_ROOT) / "aiapp" / "daytrade" / "bars_5m"


def _jst_today() -> _date:
    return timezone.localdate()


def _yf_symbol(code: str) -> str:
    code = str(code).strip()
    if not code:
        return ""
    if code.endswith(".T"):
        return code
    return f"{code}.T"


def _cache_path(code: str, d: _date) -> Path:
    return DAYTRADE_BARS_5M_DIR / str(code) / f"{d.strftime('%Y%m%d')}.parquet"


def _ensure_jst_index(idx) -> pd.DatetimeIndex:
    """
    yfinanceのindexをJSTに正規化する。
    """
    try:
        if getattr(idx, "tz", None) is None:
            return idx.tz_localize("Asia/Tokyo")
        return idx.tz_convert("Asia/Tokyo")
    except Exception:
        # 最悪 tz なしのまま（ただし dt の比較がズレるので、可能なら直す）
        return pd.DatetimeIndex(idx)


def _calc_intraday_vwap(df: pd.DataFrame) -> pd.Series:
    """
    その日の累積VWAP（近似）を作る。
    - typical price = (H+L+C)/3
    - vwap = cumsum(tp*vol) / cumsum(vol)
    """
    if df.empty:
        return pd.Series(dtype="float64")

    if "volume" not in df.columns:
        return pd.Series([None] * len(df), index=df.index, dtype="float64")

    vol = pd.to_numeric(df["volume"], errors="coerce").fillna(0.0)
    tp = (pd.to_numeric(df["high"], errors="coerce") +
          pd.to_numeric(df["low"], errors="coerce") +
          pd.to_numeric(df["close"], errors="coerce")) / 3.0

    pv = (tp * vol).fillna(0.0)
    cum_vol = vol.cumsum()
    cum_pv = pv.cumsum()

    # ゼロ割り回避
    vwap = cum_pv / cum_vol.replace(0.0, pd.NA)
    return vwap.astype("float64")


def load_daytrade_5m_bars(code: str, trade_date: _date, force_refresh: bool = False) -> pd.DataFrame:
    """
    指定銘柄・指定日（JST）1日分の 5分足を返す（vwap付き）。

    戻り値の DataFrame カラム:
      dt      : datetime64[ns, Asia/Tokyo]
      open    : float
      high    : float
      low     : float
      close   : float
      volume  : float
      vwap    : float（累積近似）
    """
    if not trade_date:
        return pd.DataFrame()

    today = _jst_today()
    if trade_date > today:
        logger.info(f"[daytrade_bars_5m] skip future date {trade_date} for {code}")
        return pd.DataFrame()

    symbol = _yf_symbol(code)
    if not symbol:
        return pd.DataFrame()

    path = _cache_path(code, trade_date)
    path.parent.mkdir(parents=True, exist_ok=True)

    # 1) キャッシュ
    if path.exists() and not force_refresh:
        try:
            df = pd.read_parquet(path)
            need = {"dt", "open", "high", "low", "close", "volume", "vwap"}
            if need.issubset(df.columns):
                return df
        except Exception as e:
            logger.warning(f"[daytrade_bars_5m] failed to read cache {path}: {e}")

    # 2) yfinance取得（当日0:00〜翌日0:00）
    start = _dt.combine(trade_date, _time(0, 0))
    end = start + _td(days=1)

    try:
        yf_df = yf.download(
            symbol,
            interval="5m",
            start=start,
            end=end,
            auto_adjust=False,
            progress=False,
        )
    except Exception as e:
        logger.info(f"[daytrade_bars_5m] yf.download failed for {symbol} {trade_date}: {e}")
        return pd.DataFrame()

    if yf_df is None or yf_df.empty:
        logger.info(f"[daytrade_bars_5m] no data from yfinance for {symbol} {trade_date}")
        return pd.DataFrame()

    idx = _ensure_jst_index(yf_df.index)

    df = yf_df[["Open", "High", "Low", "Close", "Volume"]].copy()
    df.insert(0, "dt", idx)
    df.rename(
        columns={"Open": "open", "High": "high", "Low": "low", "Close": "close", "Volume": "volume"},
        inplace=True,
    )

    # その日（JST）だけに絞る（念のため）
    try:
        d0 = pd.Timestamp(trade_date, tz="Asia/Tokyo")
        d1 = d0 + pd.Timedelta(days=1)
        df = df[(df["dt"] >= d0) & (df["dt"] < d1)]
    except Exception:
        pass

    # vwap（累積近似）付与
    df["vwap"] = _calc_intraday_vwap(df)

    # 書き込み（失敗しても致命的ではない）
    try:
        df.to_parquet(path, index=False)
    except Exception as e:
        logger.warning(f"[daytrade_bars_5m] failed to write cache {path}: {e}")

    return df