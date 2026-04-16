# =========================================================
# [FILE] ma_distance_service.py
# [PATH] <project_root>/tradeai/services/indicators/ma_distance_service.py
#
# このファイルは何？
# - 終値が 25日線からどれだけ離れているかを判定するサービスです。
# - 上がりすぎ / 下がりすぎ / ほどよい位置 を返します。
# =========================================================

from __future__ import annotations

from typing import Any


def _sma(values: list[float], window: int) -> float | None:
    if len(values) < window or window <= 0:
        return None
    target = values[-window:]
    return sum(float(v) for v in target) / float(window)


def analyze_ma_distance(closes: list[float], window: int = 25) -> dict[str, Any]:
    if len(closes) < window:
        return {
            "ma_distance_state": "UNKNOWN",
            "ma_distance_label": "不明",
            "ma_value": None,
            "distance_pct": None,
        }

    ma_value = _sma(closes, window)
    last_close = float(closes[-1])

    if ma_value is None or ma_value <= 0:
        return {
            "ma_distance_state": "UNKNOWN",
            "ma_distance_label": "不明",
            "ma_value": ma_value,
            "distance_pct": None,
        }

    distance_pct = ((last_close - ma_value) / ma_value) * 100.0

    if distance_pct >= 10:
        state = "STRETCHED_UP"
        label = "かなり上がりすぎ"
    elif distance_pct >= 5:
        state = "SLIGHTLY_STRETCHED_UP"
        label = "やや上がりすぎ"
    elif distance_pct <= -10:
        state = "STRETCHED_DOWN"
        label = "かなり下がりすぎ"
    elif distance_pct <= -5:
        state = "SLIGHTLY_STRETCHED_DOWN"
        label = "やや下がりすぎ"
    else:
        state = "NEUTRAL"
        label = "離れすぎではない"

    return {
        "ma_distance_state": state,
        "ma_distance_label": label,
        "ma_value": round(ma_value, 4),
        "distance_pct": round(distance_pct, 2),
    }