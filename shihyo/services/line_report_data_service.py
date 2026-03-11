"""
[FILE] line_report_data_service.py
[PATH] <project_root>/shihyo/services/line_report_data_service.py

このファイルは何？
- LINE用の指標レポートに必要なデータを集めて、
  送信しやすい中間データ(dict)へ整形するサービスです。
- 役割は「何を表示するか」を決めることで、
  Flex Message の見た目や LINE 送信処理は持ちません。

今回のポイント：
- views.py と同じ思想で preopen / open_1000 / close を選ぶ
- 妥当でない open_1000 prediction は除外する
- 4指標 / リスク / AI予想 / 市場の偏り を1つの report context にまとめる
"""

from __future__ import annotations

from datetime import time as dt_time
from typing import Any

from django.utils import timezone

from shihyo.models import (
    MarketIndicatorSnapshot,
    ShihyoMarketBiasSnapshot,
    ShihyoPreopenPredictionSnapshot,
)


def _safe_float(value, default: float | None = None) -> float | None:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _extract_nikkei_spot(raw_payload: dict | None) -> tuple[float, float, float, float, bool, str]:
    if not isinstance(raw_payload, dict):
        return 0.0, 0.0, 0.0, 0.0, False, "unknown"

    nikkei_spot = raw_payload.get("nikkei_spot") or {}
    available = bool(nikkei_spot.get("available"))
    source_name = str(nikkei_spot.get("source") or "unknown")

    close_value = _safe_float(nikkei_spot.get("close"), 0.0) or 0.0
    calc_dict = nikkei_spot.get("calc") or {}
    change_value = _safe_float(calc_dict.get("change"), 0.0) or 0.0
    change_pct = _safe_float(calc_dict.get("pct"), 0.0) or 0.0
    previous_close = _safe_float(nikkei_spot.get("previous_close"), 0.0) or 0.0

    if not available or close_value <= 0:
        return 0.0, 0.0, 0.0, 0.0, False, source_name

    return close_value, change_value, change_pct, previous_close, True, source_name


def _risk_badge_meta(action_title: str) -> tuple[str, str]:
    title = str(action_title or "").strip()
    if "守る" in title:
        return title or "守り", "#4B1E24"
    if "様子見" in title:
        return title or "様子見", "#4C3C12"
    if "攻め" in title:
        return title or "攻め", "#183B2A"
    return title or "判定中", "#2B3348"


def _prediction_slot_label(slot: str | None) -> str:
    if slot == ShihyoPreopenPredictionSnapshot.SLOT_OPEN_1000:
        return "10時再予想"
    return "朝予想"


def _mode_label(mode: str | None) -> str:
    if mode == ShihyoMarketBiasSnapshot.MODE_PREOPEN:
        return "朝予報"
    if mode == ShihyoMarketBiasSnapshot.MODE_OPEN_1000:
        return "10:00実績"
    if mode == ShihyoMarketBiasSnapshot.MODE_CLOSE:
        return "引け後実績"
    return "保存値"


def _tone_label_and_color(tone: str | None, summary_title: str | None) -> tuple[str, str]:
    t = str(tone or "").strip()
    title = str(summary_title or "").strip() or "まちまち"

    if t == ShihyoMarketBiasSnapshot.TONE_RISK_ON:
        return title, "#183B2A"
    if t == ShihyoMarketBiasSnapshot.TONE_RISK_OFF:
        return title, "#4B1E24"
    return title, "#4C3C12"


def _is_valid_open_1000_prediction(pred: ShihyoPreopenPredictionSnapshot | None) -> bool:
    if pred is None:
        return False

    if pred.slot != ShihyoPreopenPredictionSnapshot.SLOT_OPEN_1000:
        return True

    raw_payload = pred.raw_prediction_payload if isinstance(pred.raw_prediction_payload, dict) else {}
    source = str(raw_payload.get("current_n225_source") or "").strip()

    allowed_sources = {
        "yahoo_intraday_1000",
        "market_indicator_snapshot_1000_window",
    }
    return source in allowed_sources


def _latest_prediction_for_slot(slot: str, today_local):
    qs = (
        ShihyoPreopenPredictionSnapshot.objects
        .filter(slot=slot, trade_date__lte=today_local)
        .order_by("-trade_date", "-predicted_at")
    )

    if slot != ShihyoPreopenPredictionSnapshot.SLOT_OPEN_1000:
        return qs.first()

    for obj in qs:
        if _is_valid_open_1000_prediction(obj):
            return obj
    return None


def _today_prediction_for_slot(slot: str, today_local):
    qs = (
        ShihyoPreopenPredictionSnapshot.objects
        .filter(slot=slot, trade_date=today_local)
        .order_by("-predicted_at")
    )

    if slot != ShihyoPreopenPredictionSnapshot.SLOT_OPEN_1000:
        return qs.first()

    for obj in qs:
        if _is_valid_open_1000_prediction(obj):
            return obj
    return None


def _select_ai_prediction_snapshot(now_local):
    today_local = now_local.date()
    current_time = now_local.time()

    today_preopen = _today_prediction_for_slot(
        ShihyoPreopenPredictionSnapshot.SLOT_PREOPEN_0700,
        today_local,
    )
    today_open = _today_prediction_for_slot(
        ShihyoPreopenPredictionSnapshot.SLOT_OPEN_1000,
        today_local,
    )
    latest_preopen = _latest_prediction_for_slot(
        ShihyoPreopenPredictionSnapshot.SLOT_PREOPEN_0700,
        today_local,
    )
    latest_open = _latest_prediction_for_slot(
        ShihyoPreopenPredictionSnapshot.SLOT_OPEN_1000,
        today_local,
    )

    if current_time < dt_time(10, 0):
        return today_preopen or latest_preopen or today_open or latest_open

    if current_time < dt_time(15, 30):
        return today_open or latest_open or today_preopen or latest_preopen

    return today_open or today_preopen or latest_open or latest_preopen


def _select_market_bias_snapshot(now_local):
    today_local = now_local.date()
    current_time = now_local.time()

    today_preopen = (
        ShihyoMarketBiasSnapshot.objects
        .filter(date=today_local, mode=ShihyoMarketBiasSnapshot.MODE_PREOPEN)
        .order_by("-updated_at")
        .first()
    )
    today_open_1000 = (
        ShihyoMarketBiasSnapshot.objects
        .filter(date=today_local, mode=ShihyoMarketBiasSnapshot.MODE_OPEN_1000)
        .order_by("-updated_at")
        .first()
    )
    today_close = (
        ShihyoMarketBiasSnapshot.objects
        .filter(date=today_local, mode=ShihyoMarketBiasSnapshot.MODE_CLOSE)
        .order_by("-updated_at")
        .first()
    )
    latest_close = (
        ShihyoMarketBiasSnapshot.objects
        .filter(mode=ShihyoMarketBiasSnapshot.MODE_CLOSE)
        .order_by("-date", "-updated_at")
        .first()
    )

    if current_time < dt_time(10, 0):
        for obj in [today_preopen, today_open_1000, today_close, latest_close]:
            if obj:
                return obj
        return None

    if current_time < dt_time(15, 30):
        for obj in [today_open_1000, today_preopen, today_close, latest_close]:
            if obj:
                return obj
        return None

    for obj in [today_close, today_open_1000, today_preopen, latest_close]:
        if obj:
            return obj
    return None


def _build_market_cards(latest: MarketIndicatorSnapshot) -> list[dict[str, Any]]:
    nikkei_spot_last, nikkei_spot_change, nikkei_spot_pct, _, spot_available, _ = _extract_nikkei_spot(latest.raw_payload)

    return [
        {
            "title": "日経平均",
            "value": nikkei_spot_last if spot_available else None,
            "digits": 2,
            "change": nikkei_spot_change if spot_available else None,
            "change_pct": nikkei_spot_pct if spot_available else None,
            "badge_text": "現物",
            "badge_bg": "#274B35",
        },
        {
            "title": "日経先物",
            "value": _safe_float(latest.nikkei_futures_last),
            "digits": 2,
            "change": _safe_float(latest.nikkei_futures_change),
            "change_pct": _safe_float(latest.nikkei_futures_change_pct),
            "badge_text": latest.nikkei_label or "-",
            "badge_bg": "#6A2E2E",
        },
        {
            "title": "ドル円",
            "value": _safe_float(latest.usdjpy_last),
            "digits": 3,
            "change": _safe_float(latest.usdjpy_change),
            "change_pct": _safe_float(latest.usdjpy_change_pct),
            "badge_text": latest.fx_label or "-",
            "badge_bg": "#6A2E2E",
        },
        {
            "title": "VIX",
            "value": _safe_float(latest.vix_last),
            "digits": 2,
            "change": _safe_float(latest.vix_change),
            "change_pct": _safe_float(latest.vix_change_pct),
            "badge_text": latest.vix_label or "-",
            "badge_bg": "#6E5A1E",
        },
    ]


def _build_risk_context(latest: MarketIndicatorSnapshot) -> dict[str, Any]:
    risk_title, risk_bg = _risk_badge_meta(latest.action_title or "")
    action_lines = latest.action_lines or []
    risk_memo = " / ".join([str(x) for x in action_lines[:2]]) if isinstance(action_lines, list) and action_lines else "指標から総合判定を計算中"

    return {
        "title": risk_title,
        "badge_bg": risk_bg,
        "memo": risk_memo,
    }


def _build_ai_context(pred: ShihyoPreopenPredictionSnapshot | None, now_local) -> dict[str, Any]:
    if pred is None:
        return {
            "available": False,
        }

    reference_close = _safe_float(pred.reference_close_n225)
    pred_close_value = _safe_float(pred.pred_close_value)
    pred_close_pct = _safe_float(pred.pred_close_pct)
    pred_confidence = _safe_float(pred.pred_confidence)

    reasons = [
        str(pred.display_reason_1 or "").strip(),
        str(pred.display_reason_2 or "").strip(),
        str(pred.display_reason_3 or "").strip(),
    ]
    reasons = [x for x in reasons if x]

    after_close = now_local.time() >= dt_time(15, 30)
    has_actual = _safe_float(pred.actual_close_value) is not None and bool(pred.actual_direction_3)

    pred_delta_abs = None
    if reference_close is not None and pred_close_value is not None:
        pred_delta_abs = pred_close_value - reference_close

    badge_bg = "#183B2A" if pred.pred_direction == ShihyoPreopenPredictionSnapshot.PRED_UP else "#4B1E24"
    if pred.pred_direction == ShihyoPreopenPredictionSnapshot.PRED_FLAT:
        badge_bg = "#4C3C12"

    data = {
        "available": True,
        "slot_label": _prediction_slot_label(pred.slot),
        "label": pred.display_label or pred.pred_direction or "-",
        "badge_bg": badge_bg,
        "confidence": pred_confidence,
        "reference_close": reference_close,
        "pred_close_value": pred_close_value,
        "pred_close_pct": pred_close_pct,
        "pred_delta_abs": pred_delta_abs,
        "reasons": reasons,
        "has_result": False,
    }

    if after_close and has_actual:
        actual_close_value = _safe_float(pred.actual_close_value)
        actual_close_pct = _safe_float(pred.actual_close_pct)
        error_pct = _safe_float(pred.abs_error_pct)
        result_label = "的中" if pred.hit_direction is True else "外れ"
        result_bg = "#183B2A" if pred.hit_direction is True else "#4B1E24"

        actual_delta_abs = None
        if reference_close is not None and actual_close_value is not None:
            actual_delta_abs = actual_close_value - reference_close

        data.update({
            "has_result": True,
            "result_label": result_label,
            "result_bg": result_bg,
            "actual_close_value": actual_close_value,
            "actual_close_pct": actual_close_pct,
            "actual_delta_abs": actual_delta_abs,
            "error_pct": error_pct,
        })

    return data


def _build_bias_context(bias: ShihyoMarketBiasSnapshot | None) -> dict[str, Any]:
    if bias is None:
        return {
            "available": False,
        }

    tone_label, tone_bg = _tone_label_and_color(bias.tone, bias.summary_title)

    return {
        "available": True,
        "mode_label": _mode_label(bias.mode),
        "tone_label": tone_label,
        "tone_bg": tone_bg,
        "summary_text": bias.summary_text or "市場の偏りを集計中です。",
        "strong": [str(x) for x in (bias.strong_sectors or [])[:3]],
        "weak": [str(x) for x in (bias.weak_sectors or [])[:3]],
        "hot": [str(x) for x in (bias.hot_themes or [])[:3]],
        "cold": [str(x) for x in (bias.cold_themes or [])[:3]],
    }


def build_line_report_context() -> dict[str, Any] | None:
    latest = MarketIndicatorSnapshot.objects.first()
    if not latest:
        return None

    now_local = timezone.localtime()
    pred = _select_ai_prediction_snapshot(now_local)
    bias = _select_market_bias_snapshot(now_local)

    return {
        "generated_at": now_local,
        "latest": latest,
        "market_cards": _build_market_cards(latest),
        "risk": _build_risk_context(latest),
        "ai": _build_ai_context(pred, now_local),
        "bias": _build_bias_context(bias),
    }