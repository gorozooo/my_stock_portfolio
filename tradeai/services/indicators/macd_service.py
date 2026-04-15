# =========================================================
# [FILE] macd_service.py
# [PATH] <project_root>/tradeai/services/indicators/macd_service.py
#
# このファイルは何？
# - MACD を計算して、勢いの向きを初心者向けラベルで返すサービスです。
# =========================================================

from __future__ import annotations

from typing import Any


def _ema_series(values: list[float], span: int) -> list[float]:
    if not values:
        return []

    alpha = 2.0 / (span + 1.0)
    ema_values: list[float] = [float(values[0])]

    for value in values[1:]:
        prev = ema_values[-1]
        current = (float(value) * alpha) + (prev * (1.0 - alpha))
        ema_values.append(current)

    return ema_values


def analyze_macd(
    closes: list[float],
    short_span: int = 12,
    long_span: int = 26,
    signal_span: int = 9,
) -> dict[str, Any]:
    if len(closes) < long_span + signal_span:
        return {
            "macd": None,
            "signal": None,
            "prev_macd": None,
            "prev_signal": None,
            "macd_state": "UNKNOWN",
            "macd_label": "不明",
        }

    ema_short = _ema_series(closes, short_span)
    ema_long = _ema_series(closes, long_span)

    macd_line = [s - l for s, l in zip(ema_short, ema_long)]
    signal_line = _ema_series(macd_line, signal_span)

    if len(macd_line) < 2 or len(signal_line) < 2:
        return {
            "macd": None,
            "signal": None,
            "prev_macd": None,
            "prev_signal": None,
            "macd_state": "UNKNOWN",
            "macd_label": "不明",
        }

    prev_macd = float(macd_line[-2])
    prev_signal = float(signal_line[-2])
    macd = float(macd_line[-1])
    signal = float(signal_line[-1])

    if prev_macd <= prev_signal and macd > signal:
        state = "BULLISH_CROSS"
        label = "勢いが上向きに転換"
    elif prev_macd >= prev_signal and macd < signal:
        state = "BEARISH_CROSS"
        label = "勢いが下向きに転換"
    elif macd > signal:
        state = "BULLISH_ABOVE"
        label = "勢いは上向き"
    elif macd < signal:
        state = "BEARISH_BELOW"
        label = "勢いは下向き"
    else:
        state = "FLAT"
        label = "勢いは横ばい"

    return {
        "macd": round(macd, 4),
        "signal": round(signal, 4),
        "prev_macd": round(prev_macd, 4),
        "prev_signal": round(prev_signal, 4),
        "macd_state": state,
        "macd_label": label,
    }