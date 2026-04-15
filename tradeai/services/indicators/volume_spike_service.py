# =========================================================
# [FILE] volume_spike_service.py
# [PATH] <project_root>/tradeai/services/indicators/volume_spike_service.py
#
# このファイルは何？
# - 出来高急増を判定するサービスです。
# - 直近出来高が、少し多い / 急増 / 静か のどれかを返します。
# =========================================================

from __future__ import annotations

from typing import Any


def analyze_volume_spike(volumes: list[float], lookback: int = 20) -> dict[str, Any]:
    if len(volumes) < lookback + 1:
        return {
            "volume_ratio": None,
            "volume_state": "UNKNOWN",
            "volume_label": "不明",
        }

    prev_window = [float(v) for v in volumes[-lookback - 1:-1]]
    current_volume = float(volumes[-1])

    if not prev_window:
        return {
            "volume_ratio": None,
            "volume_state": "UNKNOWN",
            "volume_label": "不明",
        }

    avg_volume = sum(prev_window) / float(len(prev_window)) if prev_window else 0.0
    if avg_volume <= 0:
        return {
            "volume_ratio": None,
            "volume_state": "UNKNOWN",
            "volume_label": "不明",
        }

    ratio = current_volume / avg_volume

    if ratio >= 2.0:
        state = "SPIKE"
        label = "出来高急増"
    elif ratio >= 1.3:
        state = "ACTIVE"
        label = "やや活発"
    elif ratio <= 0.7:
        state = "QUIET"
        label = "かなり静か"
    else:
        state = "NORMAL"
        label = "ふつう"

    return {
        "volume_ratio": round(ratio, 2),
        "volume_state": state,
        "volume_label": label,
    }