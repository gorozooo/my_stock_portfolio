"""
[FILE] views.py
[PATH] <project_root>/shihyo/views.py

このファイルは何？
- 指標専用ダッシュボード（iPhone向け1画面）を表示するViewです。
- 最新スナップショットを1件だけ表示します。
- 最新スナップショットの実データから、
  1) リスクメーター用の score / title / needle角度
  2) 日経平均AI予想カード用の label / 予想値 / 乖離率 / 根拠
  3) 市場の偏りカード用の表示データ
  を計算してテンプレートへ渡します。

今回の市場の偏りカードについて：
- 現在の取得処理で market_bias データが raw_payload にあれば表示
- 無ければ UI は表示しつつ「取得準備中」にする
- 推測で業種やテーマは作らない
"""

from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from shihyo.models import MarketIndicatorSnapshot


def _clamp(value: float, min_value: float, max_value: float) -> float:
    return max(min_value, min(max_value, value))


def _format_price(value: float | None, digits: int = 1) -> str:
    if value is None:
        return "-"
    return f"{value:,.{digits}f}"


def _safe_float(value, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _format_signed_number(value: float | None, digits: int = 1) -> str:
    if value is None:
        return "-"
    if value > 0:
        return f"+{value:,.{digits}f}"
    return f"{value:,.{digits}f}"


def _format_signed_percent(value: float | None, digits: int = 2) -> str:
    if value is None:
        return "-"
    if value > 0:
        return f"+{value:.{digits}f}%"
    return f"{value:.{digits}f}%"


def _delta_class(value: float | None, vix_mode: bool = False) -> str:
    if value is None:
        return "change-flat"

    if not vix_mode:
        if value > 0:
            return "change-up"
        if value < 0:
            return "change-down"
        return "change-flat"

    if value > 0:
        return "vix-change-up"
    if value < 0:
        return "vix-change-down"
    return "change-flat"


def _extract_nikkei_spot(raw_payload: dict | None) -> tuple[float, float, float, bool, str]:
    """
    raw_payload から日経平均（現物）の close / change / change_pct を取り出す。
    戻り値:
      (close, change, change_pct, available, source_name)
    """
    if not isinstance(raw_payload, dict):
        return 0.0, 0.0, 0.0, False, "unknown"

    nikkei_spot = raw_payload.get("nikkei_spot") or {}
    available = bool(nikkei_spot.get("available"))
    source_name = str(nikkei_spot.get("source") or "unknown")

    close_value = _safe_float(nikkei_spot.get("close"), 0.0)
    calc_dict = nikkei_spot.get("calc") or {}
    change_value = _safe_float(calc_dict.get("change"), 0.0)
    change_pct = _safe_float(calc_dict.get("pct"), 0.0)

    if not available or close_value <= 0:
        return 0.0, 0.0, 0.0, False, source_name

    return close_value, change_value, change_pct, True, source_name


def _build_risk_context(latest: MarketIndicatorSnapshot) -> dict:
    nikkei_pct = _safe_float(latest.nikkei_futures_change_pct, 0.0)
    fx_pct = _safe_float(latest.usdjpy_change_pct, 0.0)
    vix_last = _safe_float(latest.vix_last, 0.0)
    vix_pct = _safe_float(latest.vix_change_pct, 0.0)

    score = 50.0
    score += max(0.0, -nikkei_pct) * 10.0
    score -= max(0.0, nikkei_pct) * 8.0

    score += max(0.0, -fx_pct) * 20.0
    score -= max(0.0, fx_pct) * 12.0

    if vix_last > 15:
        score += (vix_last - 15.0) * 1.7

    score += max(0.0, vix_pct) * 0.6
    score -= max(0.0, -vix_pct) * 0.35

    action_title = latest.action_title or ""
    if "守る" in action_title:
        score += 8.0
    elif "様子見" in action_title:
        score += 0.0
    else:
        score -= 8.0

    score = int(round(_clamp(score, 0.0, 100.0)))
    needle_deg = round(-58.0 + (score / 100.0) * 116.0, 1)

    if score >= 67:
        title = "今は守る相場"
        badge_class = "risk-badge-red"
    elif score >= 34:
        title = "今は様子見"
        badge_class = "risk-badge-yellow"
    else:
        title = "今は攻める相場"
        badge_class = "risk-badge-green"

    lines = latest.action_lines or []
    if isinstance(lines, list) and lines:
        summary = " / ".join([str(x) for x in lines[:2]])
    else:
        summary = "指標から総合判定を計算中"

    return {
        "score": score,
        "needle_deg": needle_deg,
        "title": title,
        "badge_class": badge_class,
        "summary": summary,
    }


def _build_ai_prediction_context(latest: MarketIndicatorSnapshot, risk: dict) -> dict:
    nikkei_spot_last, nikkei_spot_change, nikkei_spot_pct, spot_available, spot_source = _extract_nikkei_spot(latest.raw_payload)

    nikkei_futures_pct = _safe_float(latest.nikkei_futures_change_pct, 0.0)
    fx_pct = _safe_float(latest.usdjpy_change_pct, 0.0)
    vix_last = _safe_float(latest.vix_last, 0.0)
    vix_pct = _safe_float(latest.vix_change_pct, 0.0)
    risk_score = int(risk["score"])

    if not spot_available or nikkei_spot_last <= 0:
        return {
            "subtitle": "日経平均ベース短期シナリオ",
            "label": "取得待ち",
            "badge_class": "ai-pred-badge ai-pred-badge-wait",
            "predicted_value": "-",
            "current_value": "-",
            "gap_pct": "-",
            "gap_class": "ai-main-value-wait",
            "confidence": "-",
            "updated_at": latest.created_at.strftime("%H:%M") if latest.created_at else "-",
            "reason": f"日経平均現物が取得できないため予想を停止中（source={spot_source}）。先物では代用しません。",
            "predicted_delta_display": "-",
            "predicted_delta_class": "change-flat",
            "current_delta_display": "-",
            "current_delta_class": "change-flat",
        }

    signal = 0.0
    signal += nikkei_spot_pct * 1.35
    signal += nikkei_futures_pct * 1.60
    signal += fx_pct * 3.20
    signal += (-vix_pct) * 0.18

    if vix_last > 20:
        signal -= (vix_last - 20.0) * 0.18
    elif 0 < vix_last < 15:
        signal += (15.0 - vix_last) * 0.08

    if risk_score >= 70:
        signal -= 0.8
    elif risk_score <= 30:
        signal += 0.5

    gap_pct_value = _clamp(signal * 0.55, -2.80, 2.80)

    if gap_pct_value >= 0.35:
        label = "上昇予想"
        badge_class = "ai-pred-badge ai-pred-badge-up"
        gap_class = "ai-main-value-up"
    elif gap_pct_value <= -0.35:
        label = "下落予想"
        badge_class = "ai-pred-badge ai-pred-badge-down"
        gap_class = "ai-main-value-down"
    else:
        label = "様子見"
        badge_class = "ai-pred-badge ai-pred-badge-wait"
        gap_class = "ai-main-value-wait"

    predicted_value = nikkei_spot_last * (1.0 + (gap_pct_value / 100.0))
    predicted_delta_abs = predicted_value - nikkei_spot_last

    confidence_value = 52 + min(28, int(abs(signal) * 8))
    confidence_value = int(_clamp(confidence_value, 50, 80))

    reasons: list[str] = []

    if nikkei_spot_pct > 0.2:
        reasons.append("日経平均が堅調")
    elif nikkei_spot_pct < -0.2:
        reasons.append("日経平均が重い")

    if nikkei_futures_pct > 0.3:
        reasons.append("先物が先行して上向き")
    elif nikkei_futures_pct < -0.3:
        reasons.append("先物が先行して弱い")

    if fx_pct > 0.15:
        reasons.append("ドル円が追い風")
    elif fx_pct < -0.15:
        reasons.append("円高が重し")

    if vix_pct < -3.0:
        reasons.append("VIX低下で警戒後退")
    elif vix_pct > 3.0:
        reasons.append("VIX上昇で警戒増加")

    if vix_last >= 25:
        reasons.append("VIX水準が高く不安定")
    elif 0 < vix_last < 18:
        reasons.append("VIX水準は比較的落ち着き")

    if not reasons:
        reasons.append("主要指標は中立圏")

    reason_text = " / ".join(reasons[:3])

    predicted_delta_display = (
        f"{_format_signed_number(predicted_delta_abs, 1)} "
        f"({_format_signed_percent(gap_pct_value, 2)})"
    )
    current_delta_display = (
        f"{_format_signed_number(nikkei_spot_change, 1)} "
        f"({_format_signed_percent(nikkei_spot_pct, 2)})"
    )

    return {
        "subtitle": "日経平均ベース短期シナリオ",
        "label": label,
        "badge_class": badge_class,
        "predicted_value": _format_price(predicted_value, 1),
        "current_value": _format_price(nikkei_spot_last, 1),
        "gap_pct": _format_signed_percent(gap_pct_value, 2),
        "gap_class": gap_class,
        "confidence": f"{confidence_value}%",
        "updated_at": latest.created_at.strftime("%H:%M") if latest.created_at else "-",
        "reason": reason_text,
        "predicted_delta_display": predicted_delta_display,
        "predicted_delta_class": _delta_class(predicted_delta_abs),
        "current_delta_display": current_delta_display,
        "current_delta_class": _delta_class(nikkei_spot_change),
    }


def _build_market_bias_context(latest: MarketIndicatorSnapshot) -> dict:
    """
    raw_payload["market_bias"] があれば使う。
    ない場合は UI だけ出して準備中表示にする。
    """
    raw_payload = latest.raw_payload if isinstance(latest.raw_payload, dict) else {}
    market_bias = raw_payload.get("market_bias") or {}

    strong_sectors = market_bias.get("strong_sectors") or []
    weak_sectors = market_bias.get("weak_sectors") or []
    hot_themes = market_bias.get("hot_themes") or []
    cold_themes = market_bias.get("cold_themes") or []

    available = any([strong_sectors, weak_sectors, hot_themes, cold_themes])

    if not available:
        return {
            "available": False,
            "summary_title": "準備中",
            "summary_badge_class": "market-bias-badge market-bias-badge-wait",
            "summary_text": "日本株市場で、どこに資金が入っているか / どこが売られているか を表示します。",
            "strong_sectors": [],
            "weak_sectors": [],
            "hot_themes": [],
            "cold_themes": [],
        }

    summary_title = str(market_bias.get("summary_title") or "市場の偏り")
    summary_text = str(market_bias.get("summary_text") or "今日は市場の偏りが出ています。")

    tone = str(market_bias.get("tone") or "neutral")
    if tone == "risk_on":
        badge_class = "market-bias-badge market-bias-badge-on"
    elif tone == "risk_off":
        badge_class = "market-bias-badge market-bias-badge-off"
    else:
        badge_class = "market-bias-badge market-bias-badge-wait"

    return {
        "available": True,
        "summary_title": summary_title,
        "summary_badge_class": badge_class,
        "summary_text": summary_text,
        "strong_sectors": [str(x) for x in strong_sectors[:3]],
        "weak_sectors": [str(x) for x in weak_sectors[:3]],
        "hot_themes": [str(x) for x in hot_themes[:3]],
        "cold_themes": [str(x) for x in cold_themes[:3]],
    }


@login_required
def dashboard(request):
    latest = MarketIndicatorSnapshot.objects.first()

    context = {
        "latest": latest,
        "risk": None,
        "ai_pred": None,
        "market_bias": None,
    }

    if latest:
        risk = _build_risk_context(latest)
        ai_pred = _build_ai_prediction_context(latest, risk)
        market_bias = _build_market_bias_context(latest)

        context["risk"] = risk
        context["ai_pred"] = ai_pred
        context["market_bias"] = market_bias

    return render(request, "shihyo/dashboard.html", context)