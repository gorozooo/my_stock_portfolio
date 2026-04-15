# =========================================================
# [FILE] rsi_service.py
# [PATH] <project_root>/tradeai/services/indicators/rsi_service.py
#
# このファイルは何？
# - RSI(14) を計算して、初心者向けの状態ラベルも返すサービスです。
# =========================================================

from __future__ import annotations

from typing import Any


def _calc_rsi_series(closes: list[float], period: int = 14) -> list[float]:
    if len(closes) < period + 1:
        return []

    rsis: list[float] = []

    for end_idx in range(period, len(closes)):
        window = closes[end_idx - period:end_idx + 1]
        gains = 0.0
        losses = 0.0

        for i in range(1, len(window)):
            diff = float(window[i]) - float(window[i - 1])
            if diff >= 0:
                gains += diff
            else:
                losses += abs(diff)

        avg_gain = gains / float(period)
        avg_loss = losses / float(period)

        if avg_loss == 0:
            rsi = 100.0
        else:
            rs = avg_gain / avg_loss
            rsi = 100.0 - (100.0 / (1.0 + rs))

        rsis.append(rsi)

    return rsis


def analyze_rsi(closes: list[float], period: int = 14) -> dict[str, Any]:
    rsis = _calc_rsi_series(closes, period=period)

    if len(rsis) < 2:
        return {
            "rsi": None,
            "prev_rsi": None,
            "rsi_state": "UNKNOWN",
            "rsi_label": "不明",
        }

    prev_rsi = float(rsis[-2])
    rsi = float(rsis[-1])

    if prev_rsi < 35 and rsi >= 35:
        state = "REBOUND_UP"
        label = "売られすぎから持ち直し"
    elif prev_rsi > 65 and rsi <= 65:
        state = "ROLLOVER_DOWN"
        label = "買われすぎから失速"
    elif rsi >= 70:
        state = "OVERBOUGHT"
        label = "買われすぎ"
    elif rsi <= 30:
        state = "OVERSOLD"
        label = "売られすぎ"
    elif rsi >= 50:
        state = "BULLISH"
        label = "買い優勢"
    else:
        state = "BEARISH"
        label = "売り優勢"

    return {
        "rsi": round(rsi, 2),
        "prev_rsi": round(prev_rsi, 2),
        "rsi_state": state,
        "rsi_label": label,
    }