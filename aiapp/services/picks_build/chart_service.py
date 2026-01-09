# -*- coding: utf-8 -*-
"""
チャート用データ生成サービス。

- get_prices が返す DataFrame から末尾 N 本の OHLC と日付配列を抽出する
- UI側でローソク足・終値ライン・X軸表示に使う
"""

from __future__ import annotations

from typing import List, Optional, Tuple

import pandas as pd


def extract_chart_ohlc(
    raw: pd.DataFrame,
    max_points: int = 60,
) -> Tuple[
    Optional[List[float]],
    Optional[List[float]],
    Optional[List[float]],
    Optional[List[float]],
    Optional[List[str]],
]:
    if raw is None:
        return None, None, None, None, None
    try:
        df = raw.copy()
    except Exception:
        return None, None, None, None, None

    if len(df) == 0:
        return None, None, None, None, None

    def col_name(candidates):
        for c in candidates:
            if c in df.columns:
                return c
        return None

    col_o = col_name(["Open", "open", "OPEN"])
    col_h = col_name(["High", "high", "HIGH"])
    col_l = col_name(["Low", "low", "LOW"])
    col_c = col_name(["Close", "close", "CLOSE"])

    if not (col_o and col_h and col_l and col_c):
        return None, None, None, None, None

    df = df[[col_o, col_h, col_l, col_c]].tail(max_points)

    opens = [float(v) for v in df[col_o].tolist()]
    highs = [float(v) for v in df[col_h].tolist()]
    lows = [float(v) for v in df[col_l].tolist()]
    closes = [float(v) for v in df[col_c].tolist()]

    if not closes:
        return None, None, None, None, None

    dates: List[str] = []
    try:
        if isinstance(df.index, pd.DatetimeIndex):
            dates = [d.strftime("%Y-%m-%d") for d in df.index]
        else:
            idx_dt = pd.to_datetime(df.index, errors="coerce")
            for d in idx_dt:
                if pd.isna(d):
                    dates.append("")
                else:
                    dates.append(d.strftime("%Y-%m-%d"))
    except Exception:
        dates = []

    return opens, highs, lows, closes, (dates or None)