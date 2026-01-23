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
- index は必ず捨てる（dt列が唯一の時系列）
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


def _calc_intraday_vwap(df: pd.DataFrame) -> pd.Series:
    """
    その日の累積VWAP（近似）を作る。
    - typical price = (H+L+C)/3
    - vwap = cumsum(tp*vol) / cumsum(vol)

    方針（安全側）
    - volume 欠損は 0 扱い
    - cum_vol が 0 の区間は vwap を NaN にする（NATypeにしない）
    """
    if df is None or df.empty:
        return pd.Series([], dtype="float64")

    need_cols = {"high", "low", "close", "volume"}
    if not need_cols.issubset(df.columns):
        return pd.Series([np.nan] * len(df), dtype="float64")

    # 必ず Series に寄せる（たまに変な型が混ざるのを潰す）
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

    # cum_vol==0 は NaN（pandas.NA にしない）
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
                # index癖はここで必ず殺す
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

    # yfinance index を JST に
    idx = _ensure_jst_index(yf_df.index)

    # 必要列を作る（yfinance の戻りが変な型でも耐える）
    cols_map = {"Open": "open", "High": "high", "Low": "low", "Close": "close", "Volume": "volume"}
    keep = [c for c in ["Open", "High", "Low", "Close", "Volume"] if c in yf_df.columns]
    df = yf_df[keep].copy()

    # dt列を先頭に
    df.insert(0, "dt", idx)

    # リネーム（存在するものだけ）
    df.rename(columns={k: v for k, v in cols_map.items() if k in df.columns}, inplace=True)

    # その日（JST）だけに絞る（念のため）
    try:
        d0 = pd.Timestamp(trade_date, tz="Asia/Tokyo")
        d1 = d0 + pd.Timedelta(days=1)
        df = df[(df["dt"] >= d0) & (df["dt"] < d1)]
    except Exception:
        pass

    # 数値化（欠損は残す／volumeだけ0寄せ）
    for c in ["open", "high", "low", "close"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    if "volume" in df.columns:
        df["volume"] = pd.to_numeric(df["volume"], errors="coerce").fillna(0.0)

    # dt欠損やOHLC欠損は落とす（安全側）
    if "dt" in df.columns:
        df = df.dropna(subset=["dt"])
    for c in ["open", "high", "low", "close"]:
        if c in df.columns:
            df = df.dropna(subset=[c])

    # index癖は必ず捨てる（ここが今回の主眼）
    df = df.reset_index(drop=True)
    df = df.sort_values("dt").reset_index(drop=True)

    # vwap（累積近似）付与
    df["vwap"] = _calc_intraday_vwap(df)

    # 返却列を固定（余計な列が混ざったら排除）
    out_cols = ["dt", "open", "high", "low", "close", "volume", "vwap"]
    for c in out_cols:
        if c not in df.columns:
            # 欠けてたら空で返す（安全側）
            logger.info(f"[daytrade_bars_5m] missing required col={c} for {symbol} {trade_date}")
            return pd.DataFrame()

    df = df[out_cols].copy()

    # 書き込み（失敗しても致命的ではない）
    try:
        df.to_parquet(path, index=False)
    except Exception as e:
        logger.warning(f"[daytrade_bars_5m] failed to write cache {path}: {e}")

    return df