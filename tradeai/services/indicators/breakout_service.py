# =========================================================
# [FILE] breakout_service.py
# [PATH] <project_root>/tradeai/services/indicators/breakout_service.py
#
# このファイルは何？
# - 高値ぬけ / 安値わり を判定するサービスです。
# - 直近の節目を超えたかどうかを返します。
# =========================================================

from __future__ import annotations

from typing import Any


def analyze_breakout(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    lookback: int = 20,
) -> dict[str, Any]:
    if len(highs) < lookback + 1 or len(lows) < lookback + 1 or len(closes) < lookback + 1:
        return {
            "breakout_state": "UNKNOWN",
            "breakout_label": "不明",
            "range_high": None,
            "range_low": None,
        }

    prev_high = max(float(v) for v in highs[-lookback - 1:-1])
    prev_low = min(float(v) for v in lows[-lookback - 1:-1])
    last_close = float(closes[-1])

    if last_close > prev_high:
        state = "HIGH_BREAKOUT"
        label = "高値ぬけ"
    elif last_close < prev_low:
        state = "LOW_BREAKDOWN"
        label = "安値わり"
    elif last_close >= prev_high * 0.99:
        state = "NEAR_HIGH"
        label = "高値に近い"
    elif last_close <= prev_low * 1.01:
        state = "NEAR_LOW"
        label = "安値に近い"
    else:
        state = "INSIDE_RANGE"
        label = "まだ節目内"

    return {
        "breakout_state": state,
        "breakout_label": label,
        "range_high": round(prev_high, 4),
        "range_low": round(prev_low, 4),
    }