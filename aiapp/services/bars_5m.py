# aiapp/services/bars_5m.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional

import logging

import pandas as pd
import yfinance as yf
import pytz

logger = logging.getLogger(__name__)

JST = pytz.timezone("Asia/Tokyo")


@dataclass
class Bars5mResult:
    """
    5分足取得の結果ラッパー（今後拡張したくなったとき用）
    """
    code: str
    trade_date: date
    df: pd.DataFrame


def _to_yf_symbol(code: str) -> str:
    """
    JPX銘柄コードを yfinance 用のシンボルに変換する。
    例:
      "7508"  -> "7508.T"
      "7508.T" -> "7508.T"（そのまま）
    """
    code = str(code).strip()
    if not code:
        return ""
    if "." in code:
        return code
    return f"{code}.T"


def _normalize_index_to_jst(df: pd.DataFrame) -> pd.DataFrame:
    """
    yfinance の DataFrame の index（DatetimeIndex）を JST にそろえる。
    JPX銘柄はたいてい最初から JST になっているが、
    念のため tz naive の場合もローカライズしておく。
    """
    if df.empty:
        return df

    idx = df.index

    # DatetimeIndex でない場合はそのまま返す
    if not isinstance(idx, pd.DatetimeIndex):
        return df

    if idx.tz is None:
        # タイムゾーン情報なし → JST とみなしてローカライズ
        df = df.copy()
        df.index = df.index.tz_localize(JST)
        return df

    # すでに tz 付きなら JST に変換
    df = df.tz_convert(JST)
    return df


def get_5m_bars_range(code: str, center_date: date, horizon_days: int = 5) -> pd.DataFrame:
    """
    レベル3用の「5分足取得」メイン関数。

    仕様（今回の実装）:
      - yfinance から interval=5m で period=10d ぶん取得
      - その中から「center_date 当日(JST)の分だけ」を抽出
      - 返り値: 当日分の 5分足 DataFrame（Open/High/Low/Close/Volume）

    ※ horizon_days は将来の拡張用で、今は使っていません。
    """
    if not isinstance(center_date, date):
        raise ValueError("center_date には date 型を渡してください")

    yf_symbol = _to_yf_symbol(code)
    if not yf_symbol:
        logger.warning("get_5m_bars_range: 空のコードが渡されました")
        return pd.DataFrame()

    # いきなり start/end を絞ると取り逃しやタイムゾーンのズレで 0件になりやすいので、
    # いったん period=10d くらいでざっくり取ってから JST 日付でフィルタする。
    period_days = max(5, min(10, horizon_days + 2))
    period_str = f"{period_days}d"

    try:
        df = yf.download(
            yf_symbol,
            interval="5m",
            period=period_str,
            auto_adjust=False,   # ★ FutureWarning 回避のため明示
            progress=False,
        )
    except Exception as e:
        logger.exception("get_5m_bars_range: yfinance 取得で例外が発生: %s", e)
        return pd.DataFrame()

    if df is None or df.empty:
        logger.warning(
            "get_5m_bars_range: yfinance からデータが取得できませんでした code=%s period=%s",
            yf_symbol,
            period_str,
        )
        return pd.DataFrame()

    # JST にそろえてから、center_date 当日だけに絞り込む
    df = _normalize_index_to_jst(df)

    # DatetimeIndex ではない場合はそのまま返してしまう（念のため）
    if not isinstance(df.index, pd.DatetimeIndex):
        logger.warning(
            "get_5m_bars_range: index が DatetimeIndex ではありません code=%s", yf_symbol
        )
        return df

    mask = df.index.date == center_date
    df_day = df.loc[mask].copy()

    logger.info(
        "get_5m_bars_range: code=%s center_date=%s period=%s -> total=%d, day=%d",
        yf_symbol,
        center_date,
        period_str,
        len(df),
        len(df_day),
    )

    return df_day