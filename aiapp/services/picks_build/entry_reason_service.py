# -*- coding: utf-8 -*-
"""
entry_reason（6択ラベル）分類サービス。

- UIの分類タグ用途
- 厳密性より「一貫性」「落ちない」を優先
"""

from __future__ import annotations

from typing import Dict, List, Optional

import numpy as np
import pandas as pd

_ENTRY_REASONS = (
    "trend_follow",
    "pullback",
    "breakout",
    "reversal",
    "news",
    "mean_revert",
)


def classify_entry_reason(
    feat: pd.DataFrame,
    *,
    last: Optional[float],
    atr: Optional[float],
    entry: Optional[float],
    tp: Optional[float],
    sl: Optional[float],
    ml_tp_first: Optional[str] = None,
    ml_tp_probs: Optional[Dict[str, float]] = None,
    reason_lines: Optional[List[str]] = None,
    reason_concern: Optional[str] = None,
) -> str:
    try:
        if feat is None or len(feat) == 0:
            return "mean_revert"
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

        slope25 = g("SLOPE_25")
        rsi14 = g("RSI14")
        bb_z = g("BB_Z")
        vwap_gap = g("VWAP_GAP_PCT")
        gcross = g("GCROSS")
        dcross = g("DCROSS")

        r1 = g("RET_1")
        ret1_pct = (r1 * 100.0) if (r1 is not None) else None

        ma25 = g("MA25") or g("MA20")
        ma75 = g("MA75") or g("MA60")
        high_52w = g("HIGH_52W")
        low_52w = g("LOW_52W")

        last_f = float(last) if (last is not None and np.isfinite(float(last))) else None
        atr_f = float(atr) if (atr is not None and np.isfinite(float(atr))) else None

        text_blob = ""
        try:
            if reason_lines:
                text_blob += " ".join([str(x) for x in reason_lines if x])
            if reason_concern:
                text_blob += " " + str(reason_concern)
        except Exception:
            text_blob = ""

        def has_kw(*kws: str) -> bool:
            if not text_blob:
                return False
            for kw in kws:
                if kw and kw in text_blob:
                    return True
            return False

        # news
        if has_kw("材料", "決算", "IR", "ニュース", "上方修正", "下方修正", "増配", "減配", "自社株買", "TOB"):
            return "news"
        if ret1_pct is not None and abs(float(ret1_pct)) >= 7.0:
            return "news"

        # breakout
        if gcross is not None and gcross > 0:
            return "breakout"
        if last_f is not None and high_52w is not None:
            if last_f >= float(high_52w) * 0.995:
                return "breakout"
        if last_f is not None and ma25 is not None and ma75 is not None:
            if last_f > float(ma25) and last_f > float(ma75):
                if slope25 is not None and float(slope25) > 0:
                    if bb_z is not None and float(bb_z) >= 1.0:
                        return "breakout"

        # pullback
        if last_f is not None and ma25 is not None:
            if slope25 is not None and float(slope25) > 0:
                near_ma = (
                    abs(last_f - float(ma25)) <= (0.6 * atr_f)
                    if atr_f and atr_f > 0
                    else abs(last_f - float(ma25)) <= (0.015 * last_f)
                )
                rsi_ok = (rsi14 is None) or (40.0 <= float(rsi14) <= 60.0)
                vwap_ok = (vwap_gap is None) or (abs(float(vwap_gap)) <= 1.0)
                if near_ma and rsi_ok and vwap_ok:
                    return "pullback"

        # trend_follow
        if slope25 is not None and float(slope25) > 0:
            rsi_strong = (rsi14 is None) or (float(rsi14) >= 55.0)
            if last_f is not None and ma25 is not None:
                if last_f >= float(ma25) and rsi_strong:
                    return "trend_follow"
            if rsi_strong:
                return "trend_follow"

        # reversal
        if last_f is not None and low_52w is not None:
            if last_f <= float(low_52w) * 1.010:
                return "reversal"
        if rsi14 is not None and float(rsi14) <= 35.0:
            return "reversal"
        if bb_z is not None and float(bb_z) <= -1.2:
            return "reversal"
        if slope25 is not None and float(slope25) < 0:
            if dcross is not None and float(dcross) > 0:
                return "reversal"
            return "reversal"

        # mean_revert
        if bb_z is not None and abs(float(bb_z)) >= 1.6:
            return "mean_revert"
        if slope25 is not None and abs(float(slope25)) <= 0.02:
            return "mean_revert"

        return "mean_revert"
    except Exception:
        return "mean_revert"