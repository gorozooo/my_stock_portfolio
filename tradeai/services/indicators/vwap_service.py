# =========================================================
# [FILE] vwap_service.py
# [PATH] <project_root>/tradeai/services/indicators/vwap_service.py
#
# このファイルは何？
# - 最近の終値と出来高から、日足ベースのVWAP風指標を作るサービスです。
# - 初心者向けには「最近の売買コスト帯より上か下か」に翻訳して使います。
# =========================================================

from __future__ import annotations

from typing import Any


def _window_vwap(closes: list[float], volumes: list[float]) -> float | None:
    if not closes or not volumes or len(closes) != len(volumes):
        return None

    vol_sum = sum(float(v) for v in volumes if v is not None)
    if vol_sum <= 0:
        return None

    weighted_sum = sum(float(c) * float(v) for c, v in zip(closes, volumes))
    return weighted_sum / vol_sum


def analyze_vwap(closes: list[float], volumes: list[float], lookback: int = 20) -> dict[str, Any]:
    if len(closes) < lookback + 1 or len(volumes) < lookback + 1:
        return {
            "vwap": None,
            "prev_vwap": None,
            "vwap_state": "UNKNOWN",
            "vwap_label": "不明",
        }

    current_vwap = _window_vwap(closes[-lookback:], volumes[-lookback:])
    prev_vwap = _window_vwap(closes[-lookback - 1:-1], volumes[-lookback - 1:-1])

    if current_vwap is None or prev_vwap is None:
        return {
            "vwap": None,
            "prev_vwap": None,
            "vwap_state": "UNKNOWN",
            "vwap_label": "不明",
        }

    prev_close = float(closes[-2])
    last_close = float(closes[-1])

    if prev_close <= prev_vwap and last_close > current_vwap:
        state = "CROSS_UP"
        label = "コスト帯を上抜け"
    elif prev_close >= prev_vwap and last_close < current_vwap:
        state = "CROSS_DOWN"
        label = "コスト帯を下抜け"
    elif last_close > current_vwap:
        state = "ABOVE_VWAP"
        label = "コスト帯より上"
    elif last_close < current_vwap:
        state = "BELOW_VWAP"
        label = "コスト帯より下"
    else:
        state = "FLAT"
        label = "コスト帯付近"

    return {
        "vwap": round(current_vwap, 4),
        "prev_vwap": round(prev_vwap, 4),
        "vwap_state": state,
        "vwap_label": label,
    }