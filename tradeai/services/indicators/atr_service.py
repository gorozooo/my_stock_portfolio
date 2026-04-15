# =========================================================
# [FILE] atr_service.py
# [PATH] <project_root>/tradeai/services/indicators/atr_service.py
#
# このファイルは何？
# - ATR(14) を計算するサービスです。
# - 値幅の大きさを見て、監視コメントに反映します。
# =========================================================

from __future__ import annotations

from typing import Any


def analyze_atr(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    period: int = 14,
) -> dict[str, Any]:
    if len(highs) < period + 1 or len(lows) < period + 1 or len(closes) < period + 1:
        return {
            "atr": None,
            "atr_pct": None,
        }

    true_ranges: list[float] = []

    for i in range(len(closes)):
        high = float(highs[i])
        low = float(lows[i])

        if i == 0:
            tr = high - low
        else:
            prev_close = float(closes[i - 1])
            tr = max(
                high - low,
                abs(high - prev_close),
                abs(low - prev_close),
            )

        true_ranges.append(tr)

    last_close = float(closes[-1]) if closes else 0.0
    if last_close <= 0:
        return {
            "atr": None,
            "atr_pct": None,
        }

    atr = sum(true_ranges[-period:]) / float(period)
    atr_pct = (atr / last_close) * 100.0

    return {
        "atr": round(atr, 4),
        "atr_pct": round(atr_pct, 2),
    }