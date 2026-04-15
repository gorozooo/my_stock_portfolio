# =========================================================
# [FILE] ma_cross_service.py
# [PATH] <project_root>/tradeai/services/indicators/ma_cross_service.py
#
# このファイルは何？
# - 5日線 / 25日線 の関係から GC / DC を判定するサービスです。
# =========================================================

from __future__ import annotations

from typing import Any


def _sma(values: list[float], window: int) -> float | None:
    if len(values) < window or window <= 0:
        return None
    target = values[-window:]
    return sum(target) / float(window)


def _prev_sma(values: list[float], window: int) -> float | None:
    if len(values) < window + 1 or window <= 0:
        return None
    target = values[-window - 1:-1]
    return sum(target) / float(window)


def analyze_ma_cross(
    closes: list[float],
    short_window: int = 5,
    long_window: int = 25,
) -> dict[str, Any]:
    if len(closes) < long_window + 1:
        return {
            "ma_state": "UNKNOWN",
            "ma_label": "不明",
            "short_ma": None,
            "long_ma": None,
        }

    short_ma = _sma(closes, short_window)
    long_ma = _sma(closes, long_window)
    prev_short_ma = _prev_sma(closes, short_window)
    prev_long_ma = _prev_sma(closes, long_window)

    if short_ma is None or long_ma is None or prev_short_ma is None or prev_long_ma is None:
        return {
            "ma_state": "UNKNOWN",
            "ma_label": "不明",
            "short_ma": short_ma,
            "long_ma": long_ma,
        }

    if prev_short_ma <= prev_long_ma and short_ma > long_ma:
        state = "GOLDEN_CROSS"
        label = "GC発生"
    elif prev_short_ma >= prev_long_ma and short_ma < long_ma:
        state = "DEAD_CROSS"
        label = "DC発生"
    elif short_ma > long_ma:
        state = "ABOVE_GC"
        label = "GC上"
    elif short_ma < long_ma:
        state = "BELOW_DC"
        label = "DC下"
    else:
        state = "FLAT"
        label = "横ばい"

    return {
        "ma_state": state,
        "ma_label": label,
        "short_ma": round(short_ma, 4),
        "long_ma": round(long_ma, 4),
    }