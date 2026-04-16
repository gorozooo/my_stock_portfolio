# =========================================================
# [FILE] ichimoku_service.py
# [PATH] <project_root>/tradeai/services/indicators/ichimoku_service.py
#
# このファイルは何？
# - 一目均衡表の簡易判定を返すサービスです。
# - 雲の上 / 雲の中 / 雲の下 と、転換線・基準線の位置関係を見ます。
# - 画面では「大きな流れ」として初心者向けに翻訳して使います。
# =========================================================

from __future__ import annotations

from typing import Any


def _midpoint(highs: list[float], lows: list[float], window: int) -> float | None:
    if len(highs) < window or len(lows) < window:
        return None
    target_high = max(float(v) for v in highs[-window:])
    target_low = min(float(v) for v in lows[-window:])
    return (target_high + target_low) / 2.0


def analyze_ichimoku(highs: list[float], lows: list[float], closes: list[float]) -> dict[str, Any]:
    if len(highs) < 52 or len(lows) < 52 or len(closes) < 52:
        return {
            "ichimoku_state": "UNKNOWN",
            "ichimoku_label": "不明",
            "tenkan": None,
            "kijun": None,
            "span_a": None,
            "span_b": None,
        }

    tenkan = _midpoint(highs, lows, 9)
    kijun = _midpoint(highs, lows, 26)
    span_b = _midpoint(highs, lows, 52)

    if tenkan is None or kijun is None or span_b is None:
        return {
            "ichimoku_state": "UNKNOWN",
            "ichimoku_label": "不明",
            "tenkan": tenkan,
            "kijun": kijun,
            "span_a": None,
            "span_b": span_b,
        }

    span_a = (tenkan + kijun) / 2.0
    last_close = float(closes[-1])

    cloud_top = max(span_a, span_b)
    cloud_bottom = min(span_a, span_b)

    if last_close > cloud_top and tenkan > kijun:
        state = "ABOVE_CLOUD_BULLISH"
        label = "雲の上で上向き"
    elif last_close > cloud_top:
        state = "ABOVE_CLOUD"
        label = "雲の上"
    elif last_close < cloud_bottom and tenkan < kijun:
        state = "BELOW_CLOUD_BEARISH"
        label = "雲の下で下向き"
    elif last_close < cloud_bottom:
        state = "BELOW_CLOUD"
        label = "雲の下"
    else:
        state = "IN_CLOUD"
        label = "雲の中"

    return {
        "ichimoku_state": state,
        "ichimoku_label": label,
        "tenkan": round(tenkan, 4),
        "kijun": round(kijun, 4),
        "span_a": round(span_a, 4),
        "span_b": round(span_b, 4),
    }