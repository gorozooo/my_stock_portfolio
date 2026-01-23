# -*- coding: utf-8 -*-
"""
ファイル: aiapp/services/daytrade/bar_adapter_5m.py

これは何？
- bars_5m_daytrade.load_daytrade_5m_bars() の DataFrame を
  backtest_runner / strategies が使う types.Bar に変換するアダプタ。

方針
- dt は JST datetime
- open/high/low/close/vwap は float
- volume は float（0でもOK）
"""

from __future__ import annotations

from typing import List
import pandas as pd

from .types import Bar


def df_to_bars_5m(df: pd.DataFrame) -> List[Bar]:
    """
    df columns:
      dt, open, high, low, close, volume, vwap
    """
    if df is None or df.empty:
        return []

    need = {"dt", "open", "high", "low", "close", "volume", "vwap"}
    if not need.issubset(df.columns):
        return []

    out: List[Bar] = []
    for _, r in df.iterrows():
        try:
            out.append(
                Bar(
                    dt=r["dt"],
                    open=float(r["open"]),
                    high=float(r["high"]),
                    low=float(r["low"]),
                    close=float(r["close"]),
                    volume=float(r["volume"]),
                    vwap=float(r["vwap"]) if r["vwap"] is not None else None,
                )
            )
        except Exception:
            # 欠損や型崩れは安全側で落とす
            continue

    return out