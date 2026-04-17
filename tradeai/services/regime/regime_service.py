# =========================================================
# [FILE] regime_service.py
# [PATH] <project_root>/tradeai/services/regime/regime_service.py
#
# このファイルは何？
# - 最新の地合い（ロング追い風 / ショート追い風 / 中立 / ノイズ警戒）
#   を作るサービスです。
# - 日経平均、TOPIX連動ETF、ドル円、VIX を使って簡易判定します。
# - ダッシュボードでは、この結果を RegimeSnapshot に保存して表示します。
# =========================================================

from __future__ import annotations

from datetime import timedelta
from typing import Any

import yfinance as yf
from django.utils import timezone

from tradeai.models.regime_snapshot import RegimeSnapshot


SYMBOLS = {
    "nikkei": {"symbol": "^N225", "label": "日経平均"},
    "topix": {"symbol": "1306.T", "label": "TOPIX連動"},
    "usdjpy": {"symbol": "JPY=X", "label": "ドル円"},
    "vix": {"symbol": "^VIX", "label": "VIX"},
}


def _safe_float(value):
    if value is None:
        return None
    try:
        return float(value)
    except Exception:
        return None


def _fetch_history(symbol: str, period: str = "3mo"):
    try:
        df = yf.Ticker(symbol).history(period=period, interval="1d", auto_adjust=False)
    except Exception:
        return None

    if df is None or df.empty:
        return None

    try:
        closes = [float(v) for v in df["Close"].dropna().tolist()]
    except Exception:
        return None

    if len(closes) < 25:
        return None

    return closes


def _series_stats(closes: list[float]) -> dict[str, Any]:
    last_close = float(closes[-1])
    prev_close = float(closes[-2]) if len(closes) >= 2 else last_close
    close_5 = float(closes[-6]) if len(closes) >= 6 else closes[0]
    ma20 = sum(float(v) for v in closes[-20:]) / 20.0 if len(closes) >= 20 else None

    pct_1d = ((last_close - prev_close) / prev_close * 100.0) if prev_close else 0.0
    pct_5d = ((last_close - close_5) / close_5 * 100.0) if close_5 else 0.0

    return {
        "last_close": round(last_close, 2),
        "prev_close": round(prev_close, 2),
        "pct_1d": round(pct_1d, 2),
        "pct_5d": round(pct_5d, 2),
        "ma20": round(ma20, 2) if ma20 is not None else None,
        "above_ma20": bool(ma20 is not None and last_close >= ma20),
    }


def _build_payload() -> dict[str, Any] | None:
    payload: dict[str, Any] = {"components": []}

    for key, meta in SYMBOLS.items():
        closes = _fetch_history(meta["symbol"], period="3mo")
        if not closes:
            return None

        stats = _series_stats(closes)
        payload["components"].append(
            {
                "key": key,
                "label": meta["label"],
                "symbol": meta["symbol"],
                **stats,
            }
        )

    return payload


def _find_component(payload: dict[str, Any], key: str) -> dict[str, Any]:
    for item in payload.get("components", []):
        if item.get("key") == key:
            return item
    return {}


def _score_payload(payload: dict[str, Any]) -> tuple[float, float, list[str], list[str]]:
    long_score = 0.0
    short_score = 0.0
    long_reasons: list[str] = []
    short_reasons: list[str] = []

    nikkei = _find_component(payload, "nikkei")
    topix = _find_component(payload, "topix")
    usdjpy = _find_component(payload, "usdjpy")
    vix = _find_component(payload, "vix")

    for item, label in ((nikkei, "日経平均"), (topix, "TOPIX連動")):
        if not item:
            continue

        if item.get("above_ma20"):
            long_score += 2
            long_reasons.append(f"{label}が20日線より上")
        else:
            short_score += 2
            short_reasons.append(f"{label}が20日線より下")

        pct_5d = _safe_float(item.get("pct_5d")) or 0.0
        if pct_5d >= 1.5:
            long_score += 1
            long_reasons.append(f"{label}の5日騰落率が強め")
        elif pct_5d <= -1.5:
            short_score += 1
            short_reasons.append(f"{label}の5日騰落率が弱め")

    if usdjpy:
        pct_5d = _safe_float(usdjpy.get("pct_5d")) or 0.0
        if pct_5d >= 0.5:
            long_score += 1
            long_reasons.append("ドル円が円安寄り")
        elif pct_5d <= -0.5:
            short_score += 1
            short_reasons.append("ドル円が円高寄り")

    if vix:
        last_close = _safe_float(vix.get("last_close")) or 0.0
        pct_1d = _safe_float(vix.get("pct_1d")) or 0.0

        if last_close <= 20:
            long_score += 2
            long_reasons.append("VIXが低めで過度な警戒が弱い")
        elif last_close >= 28:
            short_score += 2
            short_reasons.append("VIXが高めで警戒が強い")

        if pct_1d <= -4:
            long_score += 1
            long_reasons.append("VIXが低下して落ち着き気味")
        elif pct_1d >= 4:
            short_score += 1
            short_reasons.append("VIXが上昇して不安が強い")

    return round(long_score, 2), round(short_score, 2), long_reasons[:4], short_reasons[:4]


def _decide_bias(long_score: float, short_score: float) -> str:
    diff = long_score - short_score

    if abs(diff) <= 1 and max(long_score, short_score) >= 4:
        return RegimeSnapshot.MarketBias.NOISY
    if diff >= 3:
        return RegimeSnapshot.MarketBias.LONG_TAILWIND
    if diff <= -3:
        return RegimeSnapshot.MarketBias.SHORT_TAILWIND
    return RegimeSnapshot.MarketBias.NEUTRAL


def _build_summary(bias: str, long_reasons: list[str], short_reasons: list[str]) -> str:
    if bias == RegimeSnapshot.MarketBias.LONG_TAILWIND:
        base = "今はロング側に追い風寄りです。"
        if long_reasons:
            base += " " + " / ".join(long_reasons[:3]) + "。"
        return base

    if bias == RegimeSnapshot.MarketBias.SHORT_TAILWIND:
        base = "今はショート側に追い風寄りです。"
        if short_reasons:
            base += " " + " / ".join(short_reasons[:3]) + "。"
        return base

    if bias == RegimeSnapshot.MarketBias.NOISY:
        base = "上にも下にも振れやすく、ノイズ警戒寄りです。"
        parts = []
        if long_reasons:
            parts.append("強い点: " + " / ".join(long_reasons[:2]))
        if short_reasons:
            parts.append("弱い点: " + " / ".join(short_reasons[:2]))
        if parts:
            base += " " + "。 ".join(parts) + "。"
        return base

    base = "今は中立寄りです。強い一方向ではありません。"
    parts = []
    if long_reasons:
        parts.append("追い風側: " + " / ".join(long_reasons[:2]))
    if short_reasons:
        parts.append("逆風側: " + " / ".join(short_reasons[:2]))
    if parts:
        base += " " + "。 ".join(parts) + "。"
    return base


def build_regime_snapshot(user) -> RegimeSnapshot | None:
    payload = _build_payload()
    if not payload:
        return None

    long_score, short_score, long_reasons, short_reasons = _score_payload(payload)
    market_bias = _decide_bias(long_score, short_score)
    summary_text = _build_summary(market_bias, long_reasons, short_reasons)

    payload["long_reasons"] = long_reasons
    payload["short_reasons"] = short_reasons

    snapshot = RegimeSnapshot.objects.create(
        user=user,
        as_of=timezone.now(),
        market_bias=market_bias,
        long_score=long_score,
        short_score=short_score,
        summary_text=summary_text,
        payload=payload,
    )
    return snapshot


def ensure_recent_regime_snapshot(user, max_age_minutes: int = 90) -> RegimeSnapshot | None:
    latest = RegimeSnapshot.objects.filter(user=user).order_by("-as_of").first()
    if latest and latest.as_of >= timezone.now() - timedelta(minutes=max_age_minutes):
        return latest

    created = build_regime_snapshot(user)
    if created:
        return created
    return latest