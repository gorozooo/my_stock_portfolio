# =========================================================
# [FILE] wick_ratio_service.py
# [PATH] <project_root>/tradeai/services/indicators/wick_ratio_service.py
#
# このファイルは何？
# - 最新ローソク足のヒゲの強さを判定するサービスです。
# - 上ヒゲ強め / 下ヒゲ強め / バランス を返します。
# =========================================================

from __future__ import annotations

from typing import Any


def analyze_wick_ratio(
    opens: list[float],
    highs: list[float],
    lows: list[float],
    closes: list[float],
) -> dict[str, Any]:
    if not opens or not highs or not lows or not closes:
        return {
            "wick_state": "UNKNOWN",
            "wick_label": "不明",
            "upper_wick_pct": None,
            "lower_wick_pct": None,
        }

    o = float(opens[-1])
    h = float(highs[-1])
    l = float(lows[-1])
    c = float(closes[-1])

    candle_range = max(h - l, 0.0)
    if candle_range <= 0:
        return {
            "wick_state": "UNKNOWN",
            "wick_label": "不明",
            "upper_wick_pct": None,
            "lower_wick_pct": None,
        }

    upper_wick = max(h - max(o, c), 0.0)
    lower_wick = max(min(o, c) - l, 0.0)

    upper_pct = (upper_wick / candle_range) * 100.0
    lower_pct = (lower_wick / candle_range) * 100.0

    if upper_pct >= 45 and upper_pct >= lower_pct + 15:
        state = "UPPER_HEAVY"
        label = "上ヒゲ強め"
    elif lower_pct >= 45 and lower_pct >= upper_pct + 15:
        state = "LOWER_HEAVY"
        label = "下ヒゲ強め"
    else:
        state = "BALANCED"
        label = "ヒゲはふつう"

    return {
        "wick_state": state,
        "wick_label": label,
        "upper_wick_pct": round(upper_pct, 2),
        "lower_wick_pct": round(lower_pct, 2),
    }