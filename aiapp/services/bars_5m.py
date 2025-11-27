# aiapp/services/bars_5m.py
# -*- coding: utf-8 -*-
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

# 5分足キャッシュの保存先
BARS_5M_DIR = Path(settings.MEDIA_ROOT) / "aiapp" / "bars_5m"


def _jst_today() -> _date:
    """JSTベースの「今日の日付」"""
    return timezone.localdate()


def _yf_symbol(code: str) -> str:
    """
    JPX銘柄コード → yfinance シンボル
    例: "7508" -> "7508.T"
    """
    code = str(code).strip()
    if not code:
        return ""
    if code.endswith(".T"):
        return code
    return f"{code}.T"


def _cache_path(code: str, d: _date) -> Path:
    """
    1銘柄・1日ごとのキャッシュファイルパス
    例: media/aiapp/bars_5m/7508/20251127.parquet
    """
    return BARS_5M_DIR / str(code) / f"{d.strftime('%Y%m%d')}.parquet"


def load_5m_bars(code: str, trade_date: _date) -> pd.DataFrame:
    """
    指定銘柄・指定営業日1日分の 5分足を返す。
    - まずローカルキャッシュ（parquet）を見て、あればそれを返す
    - 無ければ yfinance から取得してキャッシュ保存
    - 将来日付（today より後）は取得を試みず、空 DataFrame を返す

    戻り値の DataFrame カラム:
      ts      : datetime64[ns, Asia/Tokyo]  (バーの時刻)
      open    : float
      high    : float
      low     : float
      close   : float
      volume  : float or int
    """
    if not trade_date:
        return pd.DataFrame()

    today = _jst_today()
    if trade_date > today:
        # 未来日は問答無用でスキップ（YFに取りに行かない）
        logger.info(f"[bars_5m] skip future date {trade_date} for {code}")
        return pd.DataFrame()

    symbol = _yf_symbol(code)
    if not symbol:
        return pd.DataFrame()

    # キャッシュパス
    path = _cache_path(code, trade_date)
    path.parent.mkdir(parents=True, exist_ok=True)

    # 1) キャッシュヒット
    if path.exists():
        try:
            df = pd.read_parquet(path)
            # 最低限のカラムチェック
            if {"ts", "open", "high", "low", "close"}.issubset(df.columns):
                return df
        except Exception as e:
            logger.warning(f"[bars_5m] failed to read cache {path}: {e}")

    # 2) yfinance から取得
    start = _dt.combine(trade_date, _time(0, 0))
    end = start + _td(days=1)

    try:
        yf_df = yf.download(
            symbol,
            interval="5m",
            start=start,
            end=end,
            auto_adjust=False,      # FutureWarning 回避
            progress=False,
        )
    except Exception as e:
        logger.info(f"[bars_5m] yf.download failed for {symbol} {trade_date}: {e}")
        return pd.DataFrame()

    if yf_df is None or yf_df.empty:
        logger.info(f"[bars_5m] no data from yfinance for {symbol} {trade_date}")
        return pd.DataFrame()

    # yfinance 戻り値を整形
    # index: Datetime (tz付き or tzなし)
    idx = yf_df.index

    try:
        # tz付きなら JST へ変換、tzなしなら JST としてローカライズ
        if getattr(idx, "tz", None) is None:
            ts = idx.tz_localize("Asia/Tokyo")
        else:
            ts = idx.tz_convert("Asia/Tokyo")
    except Exception:
        # 失敗したらそのまま（tzなし）で持つ
        ts = idx

    df = yf_df[["Open", "High", "Low", "Close", "Volume"]].copy()
    df.insert(0, "ts", ts)
    df.rename(
        columns={
            "Open": "open",
            "High": "high",
            "Low": "low",
            "Close": "close",
            "Volume": "volume",
        },
        inplace=True,
    )

    # キャッシュ保存（失敗しても致命的ではない）
    try:
        df.to_parquet(path, index=False)
    except Exception as e:
        logger.warning(f"[bars_5m] failed to write cache {path}: {e}")

    return df