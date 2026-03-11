"""
[FILE] shihyo_send_line_report.py
[PATH] <project_root>/shihyo/management/commands/shihyo_send_line_report.py

このファイルは何？
- 指標ダッシュボードと同じ基準で、LINEへ「見やすいレポート形式」の Flex Message を送る command です。
- 送る内容は以下です。
  1) 日経平均 / 日経先物 / ドル円 / VIX
  2) AI予想（朝7時 or 10:00再予想）
  3) 市場の偏り
  4) 引け後なら予想結果（的中 / 外れ / 誤差）
- open_1000 は views.py と同じく、
  source が妥当な行だけ採用します。

必要な環境変数:
- LINE_CHANNEL_ACCESS_TOKEN
- LINE_USER_ID

使い方:
- 送信せず本文だけ確認
  python manage.py shihyo_send_line_report --dry-run
- 実際に送信
  python manage.py shihyo_send_line_report
"""

from __future__ import annotations

import json
import os
from datetime import time as dt_time
from typing import Any

import requests
from django.core.management.base import BaseCommand, CommandParser
from django.utils import timezone

from shihyo.models import (
    MarketIndicatorSnapshot,
    ShihyoMarketBiasSnapshot,
    ShihyoPreopenPredictionSnapshot,
)


# =========================
# 共通ヘルパ
# =========================
def _safe_float(value, default: float | None = None) -> float | None:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _format_price(value: float | None, digits: int = 2) -> str:
    if value is None:
        return "-"
    return f"{value:,.{digits}f}"


def _format_signed_number(value: float | None, digits: int = 2) -> str:
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


def _chunk_list(items: list[str], size: int) -> list[list[str]]:
    if size <= 0:
        return [items]
    return [items[i:i + size] for i in range(0, len(items), size)]


# =========================
# 予測選択（views.py と同じ思想）
# =========================
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


def _select_ai_prediction_snapshot() -> ShihyoPreopenPredictionSnapshot | None:
    now_local = timezone.localtime()
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


def _select_market_bias_snapshot() -> ShihyoMarketBiasSnapshot | None:
    now_local = timezone.localtime()
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


# =========================
# Flex部品
# =========================
def _text(
    text: str,
    size: str = "sm",
    color: str = "#FFFFFF",
    weight: str = "regular",
    wrap: bool = True,
    align: str | None = None,
    margin: str | None = None,
) -> dict[str, Any]:
    obj: dict[str, Any] = {
        "type": "text",
        "text": text,
        "size": size,
        "color": color,
        "weight": weight,
        "wrap": wrap,
    }
    if align:
        obj["align"] = align
    if margin:
        obj["margin"] = margin
    return obj


def _separator(margin: str = "md") -> dict[str, Any]:
    return {
        "type": "separator",
        "margin": margin,
        "color": "#2A3143",
    }


def _chip(text: str, bg: str, color: str = "#FFFFFF") -> dict[str, Any]:
    return {
        "type": "box",
        "layout": "horizontal",
        "paddingStart": "8px",
        "paddingEnd": "8px",
        "paddingTop": "4px",
        "paddingBottom": "4px",
        "cornerRadius": "12px",
        "backgroundColor": bg,
        "flex": 0,
        "contents": [
            _text(text, size="xs", color=color, weight="bold", wrap=False),
        ],
    }


def _stat_card(title: str, value: str, delta: str, badge_text: str, badge_bg: str) -> dict[str, Any]:
    return {
        "type": "box",
        "layout": "vertical",
        "backgroundColor": "#151C2D",
        "cornerRadius": "14px",
        "paddingAll": "10px",
        "flex": 1,
        "contents": [
            _text(title, size="xs", color="#AEB7CC"),
            _text(value, size="lg", color="#FFFFFF", weight="bold", margin="sm"),
            _text(delta, size="sm", color="#D8DEE9", weight="bold", margin="sm"),
            {
                "type": "box",
                "layout": "horizontal",
                "margin": "md",
                "backgroundColor": badge_bg,
                "cornerRadius": "10px",
                "paddingTop": "5px",
                "paddingBottom": "5px",
                "paddingStart": "8px",
                "paddingEnd": "8px",
                "contents": [
                    _text(badge_text, size="xs", color="#FFFFFF", weight="bold", align="center", wrap=False),
                ],
            },
        ],
    }


def _kv_row(label: str, value: str) -> dict[str, Any]:
    return {
        "type": "box",
        "layout": "baseline",
        "spacing": "sm",
        "contents": [
            {
                "type": "text",
                "text": label,
                "size": "xs",
                "color": "#AEB7CC",
                "flex": 3,
                "wrap": False,
            },
            {
                "type": "text",
                "text": value,
                "size": "xs",
                "color": "#FFFFFF",
                "weight": "bold",
                "flex": 7,
                "wrap": True,
                "align": "end",
            },
        ],
    }


def _section_title(left: str, right_chip: dict[str, Any] | None = None) -> dict[str, Any]:
    if right_chip:
        return {
            "type": "box",
            "layout": "horizontal",
            "justifyContent": "space-between",
            "alignItems": "center",
            "contents": [
                _text(left, size="md", color="#FFFFFF", weight="bold"),
                right_chip,
            ],
        }

    return {
        "type": "box",
        "layout": "horizontal",
        "contents": [
            _text(left, size="md", color="#FFFFFF", weight="bold"),
        ],
    }


def _chip_wrap(title: str, items: list[str], bg: str) -> list[dict[str, Any]]:
    chips = [_chip(x, bg) for x in items[:3]] if items else [_chip("-", "#2B3348")]

    rows = []
    for row_items in _chunk_list(chips, 2):
        rows.append(
            {
                "type": "box",
                "layout": "horizontal",
                "margin": "sm" if rows else "sm",
                "spacing": "sm",
                "contents": row_items,
            }
        )

    return [
        _text(title, size="xs", color="#AEB7CC", weight="bold"),
        {
            "type": "box",
            "layout": "vertical",
            "margin": "sm",
            "spacing": "sm",
            "contents": rows,
        },
    ]


# =========================
# altText用プレーン文
# =========================
def _build_alt_text(
    latest: MarketIndicatorSnapshot,
    pred: ShihyoPreopenPredictionSnapshot | None,
    bias: ShihyoMarketBiasSnapshot | None,
) -> str:
    now_local = timezone.localtime()
    lines: list[str] = [f"指標レポート {now_local.strftime('%Y-%m-%d %H:%M')}"]

    nikkei_spot_last, nikkei_spot_change, nikkei_spot_pct, _, spot_available, _ = _extract_nikkei_spot(latest.raw_payload)
    if spot_available:
        lines.append(
            f"日経平均 {_format_price(nikkei_spot_last, 2)} "
            f"({_format_signed_number(nikkei_spot_change, 2)} / {_format_signed_percent(nikkei_spot_pct, 2)})"
        )

    if pred:
        lines.append(
            f"{_prediction_slot_label(pred.slot)} {pred.display_label or pred.pred_direction or '-'} "
            f"{_format_price(_safe_float(pred.pred_close_value), 2)} "
            f"({_format_signed_percent(_safe_float(pred.pred_close_pct), 2)})"
        )

    if bias:
        lines.append(f"{_mode_label(bias.mode)} {bias.summary_title or '-'}")

    return " / ".join(lines)


# =========================
# Flex本文
# =========================
def _build_flex_contents(
    latest: MarketIndicatorSnapshot,
    pred: ShihyoPreopenPredictionSnapshot | None,
    bias: ShihyoMarketBiasSnapshot | None,
) -> dict[str, Any]:
    now_local = timezone.localtime()

    nikkei_spot_last, nikkei_spot_change, nikkei_spot_pct, _, spot_available, _ = _extract_nikkei_spot(latest.raw_payload)

    risk_title, risk_bg = _risk_badge_meta(latest.action_title or "")
    action_lines = latest.action_lines or []
    risk_memo = " / ".join([str(x) for x in action_lines[:2]]) if isinstance(action_lines, list) and action_lines else "指標から総合判定を計算中"

    bias_mode_label = _mode_label(bias.mode if bias else None)
    bias_tone_label, bias_tone_bg = _tone_label_and_color(
        bias.tone if bias else None,
        bias.summary_title if bias else None,
    )

    body_contents: list[dict[str, Any]] = []

    body_contents.extend([
        {
            "type": "box",
            "layout": "vertical",
            "contents": [
                _text("📡 指標レポート", size="xl", color="#FFFFFF", weight="bold"),
                _text(now_local.strftime("%Y-%m-%d %H:%M"), size="xs", color="#AEB7CC", margin="sm"),
            ],
        },
        _separator("lg"),
    ])

    body_contents.extend([
        _section_title("主要4指標"),
        {
            "type": "box",
            "layout": "horizontal",
            "spacing": "sm",
            "margin": "md",
            "contents": [
                _stat_card(
                    "日経平均",
                    _format_price(nikkei_spot_last, 2) if spot_available else "-",
                    f"{_format_signed_number(nikkei_spot_change, 2)} / {_format_signed_percent(nikkei_spot_pct, 2)}" if spot_available else "-",
                    "現物",
                    "#274B35",
                ),
                _stat_card(
                    "日経先物",
                    _format_price(_safe_float(latest.nikkei_futures_last), 2),
                    f"{_format_signed_number(_safe_float(latest.nikkei_futures_change), 2)} / {_format_signed_percent(_safe_float(latest.nikkei_futures_change_pct), 2)}",
                    latest.nikkei_label or "-",
                    "#6A2E2E",
                ),
            ],
        },
        {
            "type": "box",
            "layout": "horizontal",
            "spacing": "sm",
            "margin": "sm",
            "contents": [
                _stat_card(
                    "ドル円",
                    _format_price(_safe_float(latest.usdjpy_last), 3),
                    f"{_format_signed_number(_safe_float(latest.usdjpy_change), 3)} / {_format_signed_percent(_safe_float(latest.usdjpy_change_pct), 2)}",
                    latest.fx_label or "-",
                    "#6A2E2E",
                ),
                _stat_card(
                    "VIX",
                    _format_price(_safe_float(latest.vix_last), 2),
                    f"{_format_signed_number(_safe_float(latest.vix_change), 2)} / {_format_signed_percent(_safe_float(latest.vix_change_pct), 2)}",
                    latest.vix_label or "-",
                    "#6E5A1E",
                ),
            ],
        },
        {
            "type": "box",
            "layout": "vertical",
            "margin": "md",
            "backgroundColor": "#151C2D",
            "cornerRadius": "14px",
            "paddingAll": "12px",
            "contents": [
                {
                    "type": "box",
                    "layout": "horizontal",
                    "justifyContent": "space-between",
                    "alignItems": "center",
                    "contents": [
                        _text("リスク", size="sm", color="#AEB7CC", weight="bold"),
                        _chip(risk_title, risk_bg),
                    ],
                },
                _text(risk_memo, size="sm", color="#FFFFFF", margin="md"),
            ],
        },
        _separator("lg"),
    ])

    body_contents.append(_section_title("AI予想"))

    if pred is None:
        body_contents.append(
            {
                "type": "box",
                "layout": "vertical",
                "margin": "md",
                "backgroundColor": "#151C2D",
                "cornerRadius": "14px",
                "paddingAll": "12px",
                "contents": [
                    _text("予想データがまだありません", size="sm", color="#FFFFFF"),
                ],
            }
        )
    else:
        slot_label = _prediction_slot_label(pred.slot)
        reference_close = _safe_float(pred.reference_close_n225)
        pred_close_value = _safe_float(pred.pred_close_value)
        pred_close_pct = _safe_float(pred.pred_close_pct)
        pred_confidence = _safe_float(pred.pred_confidence)

        after_close = now_local.time() >= dt_time(15, 30)
        has_actual = _safe_float(pred.actual_close_value) is not None and bool(pred.actual_direction_3)

        if after_close and has_actual:
            actual_close_value = _safe_float(pred.actual_close_value)
            actual_close_pct = _safe_float(pred.actual_close_pct)
            hit = pred.hit_direction is True
            result_label = "的中" if hit else "外れ"
            result_bg = "#183B2A" if hit else "#4B1E24"

            error_pct = _safe_float(pred.abs_error_pct)
            main_title = pred.display_label or pred.pred_direction or "-"
            delta_abs = (actual_close_value - reference_close) if (actual_close_value is not None and reference_close is not None) else None

            body_contents.append(
                {
                    "type": "box",
                    "layout": "vertical",
                    "margin": "md",
                    "backgroundColor": "#151C2D",
                    "cornerRadius": "14px",
                    "paddingAll": "12px",
                    "contents": [
                        {
                            "type": "box",
                            "layout": "horizontal",
                            "justifyContent": "space-between",
                            "alignItems": "center",
                            "contents": [
                                _text(f"{slot_label} {main_title}", size="sm", color="#FFFFFF", weight="bold"),
                                _chip(result_label, result_bg),
                            ],
                        },
                        {
                            "type": "box",
                            "layout": "vertical",
                            "margin": "md",
                            "spacing": "sm",
                            "contents": [
                                _kv_row("基準値", _format_price(reference_close, 2)),
                                _kv_row("予想値", f"{_format_price(pred_close_value, 2)} ({_format_signed_percent(pred_close_pct, 2)})"),
                                _kv_row("実績値", f"{_format_price(actual_close_value, 2)} ({_format_signed_number(delta_abs, 2)} / {_format_signed_percent(actual_close_pct, 2)})"),
                                _kv_row("誤差", f"{error_pct:.2f}pt" if error_pct is not None else "-"),
                            ],
                        },
                    ],
                }
            )
        else:
            reasons = [
                str(pred.display_reason_1 or "").strip(),
                str(pred.display_reason_2 or "").strip(),
                str(pred.display_reason_3 or "").strip(),
            ]
            reasons = [x for x in reasons if x]

            pred_delta_abs = (pred_close_value - reference_close) if (pred_close_value is not None and reference_close is not None) else None

            badge_bg = "#183B2A" if pred.pred_direction == ShihyoPreopenPredictionSnapshot.PRED_UP else "#4B1E24"
            if pred.pred_direction == ShihyoPreopenPredictionSnapshot.PRED_FLAT:
                badge_bg = "#4C3C12"

            ai_box_contents: list[dict[str, Any]] = [
                {
                    "type": "box",
                    "layout": "horizontal",
                    "justifyContent": "space-between",
                    "alignItems": "center",
                    "contents": [
                        _text(f"{slot_label}", size="sm", color="#AEB7CC", weight="bold"),
                        _chip(pred.display_label or pred.pred_direction or "-", badge_bg),
                    ],
                },
                {
                    "type": "box",
                    "layout": "horizontal",
                    "justifyContent": "space-between",
                    "margin": "md",
                    "contents": [
                        _text("確信度", size="xs", color="#AEB7CC"),
                        _text(f"{round(pred_confidence)}%" if pred_confidence is not None else "-", size="sm", color="#FFFFFF", weight="bold"),
                    ],
                },
                {
                    "type": "box",
                    "layout": "vertical",
                    "spacing": "sm",
                    "margin": "md",
                    "contents": [
                        _kv_row("基準値", _format_price(reference_close, 2)),
                        _kv_row("予想値", f"{_format_price(pred_close_value, 2)} ({_format_signed_number(pred_delta_abs, 2)} / {_format_signed_percent(pred_close_pct, 2)})"),
                    ],
                },
                _text("理由", size="xs", color="#AEB7CC", weight="bold", margin="md"),
            ]

            for r in reasons[:3]:
                ai_box_contents.append(_text(f"・{r}", size="sm", color="#FFFFFF", margin="sm"))

            body_contents.append(
                {
                    "type": "box",
                    "layout": "vertical",
                    "margin": "md",
                    "backgroundColor": "#151C2D",
                    "cornerRadius": "14px",
                    "paddingAll": "12px",
                    "contents": ai_box_contents,
                }
            )

    body_contents.append(_separator("lg"))

    body_contents.append(
        _section_title(
            "市場の偏り",
            _chip(bias_mode_label, "#2B3348"),
        )
    )

    if bias is None:
        body_contents.append(
            {
                "type": "box",
                "layout": "vertical",
                "margin": "md",
                "backgroundColor": "#151C2D",
                "cornerRadius": "14px",
                "paddingAll": "12px",
                "contents": [
                    _text("偏りデータがまだありません", size="sm", color="#FFFFFF"),
                ],
            }
        )
    else:
        strong = [str(x) for x in (bias.strong_sectors or [])[:3]]
        weak = [str(x) for x in (bias.weak_sectors or [])[:3]]
        hot = [str(x) for x in (bias.hot_themes or [])[:3]]
        cold = [str(x) for x in (bias.cold_themes or [])[:3]]

        body_contents.append(
            {
                "type": "box",
                "layout": "vertical",
                "margin": "md",
                "backgroundColor": "#151C2D",
                "cornerRadius": "14px",
                "paddingAll": "12px",
                "contents": [
                    {
                        "type": "box",
                        "layout": "horizontal",
                        "justifyContent": "space-between",
                        "alignItems": "center",
                        "contents": [
                            _text("地合い", size="xs", color="#AEB7CC", weight="bold"),
                            _chip(bias_tone_label, bias_tone_bg),
                        ],
                    },
                    _text(bias.summary_text or "市場の偏りを集計中です。", size="sm", color="#FFFFFF", margin="md"),
                    {
                        "type": "box",
                        "layout": "vertical",
                        "margin": "md",
                        "spacing": "md",
                        "contents": (
                            _chip_wrap("強い業種", strong, "#183B2A")
                            + _chip_wrap("弱い業種", weak, "#4B1E24")
                            + _chip_wrap("値上がり偏り", hot, "#2B3348")
                            + _chip_wrap("値下がり偏り", cold, "#233A75")
                        ),
                    },
                ],
            }
        )

    body_contents.append(
        _text("※ ダッシュボードと同じ基準で作成", size="xs", color="#7F8AA3", margin="lg", align="center")
    )

    return {
        "type": "bubble",
        "size": "giga",
        "body": {
            "type": "box",
            "layout": "vertical",
            "paddingAll": "16px",
            "backgroundColor": "#0B1020",
            "contents": body_contents,
        },
    }


class Command(BaseCommand):
    help = "指標ダッシュボードと同じ基準で LINE へ shihyo レポートを Flex Message で送信します。"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="LINE送信せず altText と Flex JSON を表示する。",
        )
        parser.add_argument(
            "--to-user-id",
            type=str,
            default="",
            help="送信先LINE userId。未指定なら環境変数 LINE_USER_ID を使う。",
        )

    def handle(self, *args, **options):
        latest = MarketIndicatorSnapshot.objects.first()
        if not latest:
            self.stdout.write(self.style.ERROR("MarketIndicatorSnapshot がありません。先に shihyo_fetch を実行してください。"))
            return

        pred = _select_ai_prediction_snapshot()
        bias = _select_market_bias_snapshot()

        alt_text = _build_alt_text(latest, pred, bias)
        flex_contents = _build_flex_contents(latest, pred, bias)

        if options.get("dry_run"):
            self.stdout.write("===== altText =====")
            self.stdout.write(alt_text)
            self.stdout.write("")
            self.stdout.write("===== flex json =====")
            self.stdout.write(json.dumps(flex_contents, ensure_ascii=False, indent=2))
            return

        channel_access_token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "").strip()
        to_user_id = str(options.get("to_user_id") or os.environ.get("LINE_USER_ID") or "").strip()

        if not channel_access_token:
            self.stdout.write(self.style.ERROR("LINE_CHANNEL_ACCESS_TOKEN が設定されていません。"))
            return

        if not to_user_id:
            self.stdout.write(self.style.ERROR("送信先 userId がありません。--to-user-id または LINE_USER_ID を設定してください。"))
            return

        headers = {
            "Authorization": f"Bearer {channel_access_token}",
            "Content-Type": "application/json",
        }
        payload = {
            "to": to_user_id,
            "messages": [
                {
                    "type": "flex",
                    "altText": alt_text,
                    "contents": flex_contents,
                }
            ],
        }

        r = requests.post(
            "https://api.line.me/v2/bot/message/push",
            headers=headers,
            json=payload,
            timeout=15,
        )

        if 200 <= r.status_code < 300:
            self.stdout.write(self.style.SUCCESS("[shihyo_send_line_report] sent"))
            return

        self.stdout.write(
            self.style.ERROR(
                f"[shihyo_send_line_report] failed status={r.status_code} body={r.text}"
            )
        )