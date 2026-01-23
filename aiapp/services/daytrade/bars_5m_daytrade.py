# -*- coding: utf-8 -*-
"""
ファイル: aiapp/services/daytrade/bars_5m_daytrade.py

これは何？
- デイトレ専用の「指定銘柄・指定日」の5分足取得＆キャッシュ。
- daytrade の backtest / signal で使いやすい形（dt + vwap 付き）で返す。

仕様
- yfinance で interval="5m" を取得
- JSTへ統一
- その日（trade_date）だけに絞る
- vwap（累積近似）を生成（欠損・ゼロ出来高でも落ちない）
- index は必ず捨てる（dt列が真実）
- yfinance が MultiIndex 列を返しても必ず 1D Series に正規化して処理する
- media/aiapp/daytrade/bars_5m/<code>/YYYYMMDD.parquet に保存

注意
- 無料データは欠損・遅延があり得るので、空なら空で返す（安全側）。
"""

from __future__ import annotations

import logging
from datetime import date as _date, datetime as _dt, time as _time, timedelta as _td
from pathlib import Path

import numpy as np
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
        return pd.DatetimeIndex(idx)


def _pick_1d_series(x) -> pd.Series:
    """
    yfinance の戻りが Series でも DataFrame でも、
    「必ず 1D の Series」を返す。

    - Series -> そのまま
    - DataFrame -> 最初の列を採用（単一ティッカー想定）
    - その他 -> 空Series
    """
    if isinstance(x, pd.Series):
        return x
    if isinstance(x, pd.DataFrame):
        if x.shape[1] == 0:
            return pd.Series(dtype="float64")
        return x.iloc[:, 0]
    return pd.Series(dtype="float64")


def _calc_intraday_vwap(df: pd.DataFrame) -> pd.Series:
    """
    その日の累積VWAP（近似）を作る。
    - typical price = (H+L+C)/3
    - vwap = cumsum(tp*vol) / cumsum(vol)

    方針（安全側）
    - volume 欠損は 0 扱い
    - cum_vol が 0 の区間は vwap を NaN（NATypeにしない）
    """
    if df is None or df.empty:
        return pd.Series([], dtype="float64")

    need_cols = {"high", "low", "close", "volume"}
    if not need_cols.issubset(df.columns):
        return pd.Series([np.nan] * len(df), dtype="float64")

    vol = pd.Series(df["volume"]).copy()
    hi = pd.Series(df["high"]).copy()
    lo = pd.Series(df["low"]).copy()
    cl = pd.Series(df["close"]).copy()

    vol = pd.to_numeric(vol, errors="coerce").fillna(0.0).astype("float64")
    hi = pd.to_numeric(hi, errors="coerce").astype("float64")
    lo = pd.to_numeric(lo, errors="coerce").astype("float64")
    cl = pd.to_numeric(cl, errors="coerce").astype("float64")

    tp = (hi + lo + cl) / 3.0
    pv = (tp * vol).fillna(0.0)

    cum_vol = vol.cumsum().astype("float64")
    cum_pv = pv.cumsum().astype("float64")

    denom = cum_vol.where(cum_vol > 0.0, np.nan)
    vwap = (cum_pv / denom).astype("float64")

    return vwap


def load_daytrade_5m_bars(code: str, trade_date: _date, force_refresh: bool = False) -> pd.DataFrame:
    """
    指定銘柄・指定日（JST）1日分の 5分足を返す（vwap付き）。

    戻り値の DataFrame カラム（固定）:
      dt      : datetime64[ns, Asia/Tokyo]
      open    : float
      high    : float
      low     : float
      close   : float
      volume  : float
      vwap    : float（累積近似）

    重要
    - index は必ず捨てる（dt列が唯一の時系列）
    - yfinance が MultiIndex 列でも必ず処理できる
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
                df = df.reset_index(drop=True)
                df = df.sort_values("dt").reset_index(drop=True)
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

    # index を JST に
    idx = _ensure_jst_index(yf_df.index)

    # 重要：Open/High/Low/Close/Volume が Series でも DataFrame(MultiIndex) でも 1D に揃える
    s_open = _pick_1d_series(yf_df.get("Open"))
    s_high = _pick_1d_series(yf_df.get("High"))
    s_low = _pick_1d_series(yf_df.get("Low"))
    s_close = _pick_1d_series(yf_df.get("Close"))
    s_vol = _pick_1d_series(yf_df.get("Volume"))

    df = pd.DataFrame(
        {
            "dt": idx,
            "open": s_open.values if len(s_open) else np.nan,
            "high": s_high.values if len(s_high) else np.nan,
            "low": s_low.values if len(s_low) else np.nan,
            "close": s_close.values if len(s_close) else np.nan,
            "volume": s_vol.values if len(s_vol) else np.nan,
        }
    )

    # その日（JST）だけに絞る（念のため）
    try:
        d0 = pd.Timestamp(trade_date, tz="Asia/Tokyo")
        d1 = d0 + pd.Timedelta(days=1)
        df = df[(df["dt"] >= d0) & (df["dt"] < d1)]
    except Exception:
        pass

    # 数値化
    for c in ["open", "high", "low", "close"]:
        df[c] = pd.to_numeric(pd.Series(df[c]), errors="coerce")

    df["volume"] = pd.to_numeric(pd.Series(df["volume"]), errors="coerce").fillna(0.0)

    # 欠損を落とす（安全側）
    df = df.dropna(subset=["dt", "open", "high", "low", "close"])
    df = df.reset_index(drop=True)
    df = df.sort_values("dt").reset_index(drop=True)

    # vwap（累積近似）
    df["vwap"] = _calc_intraday_vwap(df)

    # 返却列固定
    out_cols = ["dt", "open", "high", "low", "close", "volume", "vwap"]
    df = df[out_cols].copy()

    # キャッシュ保存
    try:
        df.to_parquet(path, index=False)
    except Exception as e:
        logger.warning(f"[daytrade_bars_5m] failed to write cache {path}: {e}")

    return df