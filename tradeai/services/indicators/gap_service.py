# =========================================================
# [FILE] gap_service.py
# [PATH] <project_root>/tradeai/services/indicators/gap_service.py
#
# このファイルは何？
# - 前日終値に対する当日始値のギャップを判定するサービスです。
# - ギャップアップ / ギャップダウン / ほぼ変化なし を返します。
# =========================================================

from __future__ import annotations

from typing import Any


def analyze_gap(opens: list[float], closes: list[float]) -> dict[str, Any]:
    if len(opens) < 2 or len(closes) < 2:
        return {
            "gap_state": "UNKNOWN",
            "gap_label": "不明",
            "gap_pct": None,
        }

    today_open = float(opens[-1])
    prev_close = float(closes[-2])

    if prev_close <= 0:
        return {
            "gap_state": "UNKNOWN",
            "gap_label": "不明",
            "gap_pct": None,
        }

    gap_pct = ((today_open - prev_close) / prev_close) * 100.0

    if gap_pct >= 1.5:
        state = "GAP_UP"
        label = "ギャップアップ"
    elif gap_pct <= -1.5:
        state = "GAP_DOWN"
        label = "ギャップダウン"
    else:
        state = "SMALL_GAP"
        label = "大きなギャップなし"

    return {
        "gap_state": state,
        "gap_label": label,
        "gap_pct": round(gap_pct, 2),
    }