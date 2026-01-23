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


def _normalize_yf_df(yf_df: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """
    yfinanceの戻りが環境差で以下の揺れ方をするのを吸収する：
    - columns が MultiIndex（例：('Open','3023.T') / ('3023.T','Open')）
    - 何らかの理由で同名列が重複（df['Volume'] が DataFrame になる等）

    ここで「Open/High/Low/Close/Volume の単層カラムDataFrame」に正規化する。
    """
    if yf_df is None or yf_df.empty:
        return pd.DataFrame()

    df = yf_df

    # 1) MultiIndex columns を単層に落とす
    if isinstance(df.columns, pd.MultiIndex):
        # まず「どのレベルに Open/High... が居るか」を見て剥がす
        cols = df.columns

        # 代表的パターンA: (PriceField, Ticker)
        # 例: ('Open','3023.T') なら level0 が PriceField
        if {"Open", "High", "Low", "Close", "Volume"}.issubset(set(cols.get_level_values(0))):
            # tickerはlevel1にある想定。もし symbol 列があればそれを選ぶ
            try:
                if symbol in set(cols.get_level_values(1)):
                    df = df.xs(symbol, axis=1, level=1, drop_level=True)
                else:
                    # symbolが見つからない場合は、とりあえず先頭ティッカーを使う（安全側）
                    first = list(dict.fromkeys(cols.get_level_values(1)))[0]
                    df = df.xs(first, axis=1, level=1, drop_level=True)
            except Exception:
                # 失敗したら平坦化して後段で拾う
                df = df.copy()
                df.columns = [str(a) for a in df.columns]

        # 代表的パターンB: (Ticker, PriceField)
        # 例: ('3023.T','Open') なら level1 が PriceField
        elif {"Open", "High", "Low", "Close", "Volume"}.issubset(set(cols.get_level_values(1))):
            try:
                if symbol in set(cols.get_level_values(0)):
                    df = df.xs(symbol, axis=1, level=0, drop_level=True)
                else:
                    first = list(dict.fromkeys(cols.get_level_values(0)))[0]
                    df = df.xs(first, axis=1, level=0, drop_level=True)
            except Exception:
                df = df.copy()
                df.columns = [str(a) for a in df.columns]

        else:
            # よく分からないMultiIndexは文字列化して落とす
            df = df.copy()
            df.columns = [f"{a}_{b}" for a, b in df.columns.to_list()]

    # 2) 必須列を揃える（余計な列があってもOK）
    need = ["Open", "High", "Low", "Close", "Volume"]
    # yfinanceが小文字などで来る可能性もゼロではないので補助
    col_map = {c: c for c in df.columns}

    # 単純に need が全部あるならそのまま
    if all(n in df.columns for n in need):
        return df[need].copy()

    # 代替候補（ケース保険）
    # 例: "Adj Close" とかが混じることがあるが、5m用途では Close を最優先
    # ここは拡張できるようにしておく
    def pick(name: str) -> Optional[str]:
        if name in df.columns:
            return name
        # 大小文字違い
        for c in df.columns:
            if str(c).lower() == name.lower():
                return c
        return None

    picked = []
    for n in need:
        c = pick(n)
        if c is None:
            return pd.DataFrame()
        picked.append(c)

    out = df[picked].copy()
    out.columns = need
    return out


def _as_series(x) -> pd.Series:
    """
    df['col'] が DataFrame（重複列）になるケースを吸収して Series に落とす。
    """
    if isinstance(x, pd.DataFrame):
        # 同名列が複数ある → 先頭を採用（安全側）
        return x.iloc[:, 0]
    if isinstance(x, pd.Series):
        return x
    # それ以外はSeries化を試みる
    return pd.Series(x)


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

    vol = _as_series(df["volume"])
    high = _as_series(df["high"])
    low = _as_series(df["low"])
    close = _as_series(df["close"])

    vol = pd.to_numeric(vol, errors="coerce").fillna(0.0)
    tp = (pd.to_numeric(high, errors="coerce") +
          pd.to_numeric(low, errors="coerce") +
          pd.to_numeric(close, errors="coerce")) / 3.0

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

    # yfinance戻りを正規化（MultiIndex/重複列対策）
    yf_df = _normalize_yf_df(yf_df, symbol)
    if yf_df is None or yf_df.empty:
        logger.info(f"[daytrade_bars_5m] normalize failed or empty for {symbol} {trade_date}")
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

    if df.empty:
        logger.info(f"[daytrade_bars_5m] empty after date filter for {symbol} {trade_date}")
        return pd.DataFrame()

    # vwap（累積近似）付与
    df["vwap"] = _calc_intraday_vwap(df)

    # 書き込み（失敗しても致命的ではない）
    try:
        df.to_parquet(path, index=False)
    except Exception as e:
        logger.warning(f"[daytrade_bars_5m] failed to write cache {path}: {e}")

    return df