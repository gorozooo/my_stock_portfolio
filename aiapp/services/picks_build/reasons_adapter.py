# -*- coding: utf-8 -*-
"""
reasons サービスへ渡す入力を作るアダプタ。

- features DataFrame から必要指標だけ抜き出して、make_reasons が期待する dict に変換する
"""

from __future__ import annotations

from typing import Any, Dict, Optional

import numpy as np
import pandas as pd


def build_reasons_features(feat: pd.DataFrame, last: float, atr: float) -> Dict[str, Any]:
    if feat is None or len(feat) == 0:
        return {}

    row = feat.iloc[-1]

    def g(key: str) -> Optional[float]:
        try:
            v = row.get(key)
        except Exception:
            v = None
        if v is None:
            return None
        try:
            f = float(v)
        except Exception:
            return None
        if not np.isfinite(f):
            return None
        return f

    ema_slope = g("SLOPE_25") or g("SLOPE_20")

    rel_strength_10 = None
    r20 = g("RET_20")
    if r20 is not None:
        rel_strength_10 = r20 * 100.0

    ret1_pct = None
    r1 = g("RET_1")
    if r1 is not None:
        ret1_pct = r1 * 100.0

    rsi14 = g("RSI14")

    vol = g("Volume")
    ma_base = g("MA25") or g("MA20")
    vol_ma_ratio = None
    if vol is not None and ma_base is not None and ma_base > 0:
        vol_ma_ratio = vol / ma_base

    breakout_flag = 0
    gcross = g("GCROSS")
    if gcross is not None and gcross > 0:
        breakout_flag = 1

    vwap_proximity = g("VWAP_GAP_PCT")

    atr14 = float(atr) if np.isfinite(atr) else None
    last_price = float(last) if np.isfinite(last) else None

    return {
        "ema_slope": ema_slope,
        "rel_strength_10": rel_strength_10,
        "ret1_pct": ret1_pct,
        "rsi14": rsi14,
        "vol_ma_ratio": vol_ma_ratio,
        "breakout_flag": breakout_flag,
        "atr14": atr14,
        "vwap_proximity": vwap_proximity,
        "last_price": last_price,
    }