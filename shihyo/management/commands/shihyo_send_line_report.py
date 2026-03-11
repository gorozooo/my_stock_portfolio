"""
[FILE] shihyo_send_line_report.py
[PATH] <project_root>/shihyo/management/commands/shihyo_send_line_report.py

このファイルは何？
- 指標ダッシュボードと同じ基準で、LINEへ見やすい指標レポートを送る command です。
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

import os
from datetime import time as dt_time
from typing import Optional

import requests
from django.core.management.base import BaseCommand, CommandParser
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


def _mode_label(mode: str | None) -> str:
    if mode == ShihyoMarketBiasSnapshot.MODE_PREOPEN:
        return "朝予報"
    if mode == ShihyoMarketBiasSnapshot.MODE_OPEN_1000:
        return "10:00実績"
    if mode == ShihyoMarketBiasSnapshot.MODE_CLOSE:
        return "引け後実績"
    return "保存値"


def _prediction_slot_label(slot: str | None) -> str:
    if slot == ShihyoPreopenPredictionSnapshot.SLOT_OPEN_1000:
        return "10時再予想"
    return "朝予想"


def _build_report_text(
    latest: MarketIndicatorSnapshot,
    pred: ShihyoPreopenPredictionSnapshot | None,
    bias: ShihyoMarketBiasSnapshot | None,
) -> str:
    now_local = timezone.localtime()
    lines: list[str] = []

    lines.append(f"📡 指標レポート {now_local.strftime('%Y-%m-%d %H:%M')}")
    lines.append("")

    # --- 市況4本 ---
    nikkei_spot_last, nikkei_spot_change, nikkei_spot_pct, _, spot_available, _ = _extract_nikkei_spot(latest.raw_payload)

    if spot_available:
        lines.append(
            f"日経平均: {_format_price(nikkei_spot_last, 2)} "
            f"({_format_signed_number(nikkei_spot_change, 2)} / {_format_signed_percent(nikkei_spot_pct, 2)})"
        )
    else:
        lines.append("日経平均: -")

    lines.append(
        f"日経先物: {_format_price(_safe_float(latest.nikkei_futures_last), 2)} "
        f"({_format_signed_number(_safe_float(latest.nikkei_futures_change), 2)} / "
        f"{_format_signed_percent(_safe_float(latest.nikkei_futures_change_pct), 2)})"
    )
    lines.append(
        f"ドル円: {_format_price(_safe_float(latest.usdjpy_last), 3)} "
        f"({_format_signed_number(_safe_float(latest.usdjpy_change), 3)} / "
        f"{_format_signed_percent(_safe_float(latest.usdjpy_change_pct), 2)})"
    )
    lines.append(
        f"VIX: {_format_price(_safe_float(latest.vix_last), 2)} "
        f"({_format_signed_number(_safe_float(latest.vix_change), 2)} / "
        f"{_format_signed_percent(_safe_float(latest.vix_change_pct), 2)})"
    )
    lines.append("")

    # --- リスク ---
    lines.append(f"リスク: {latest.action_title or '-'}")
    action_lines = latest.action_lines or []
    if isinstance(action_lines, list) and action_lines:
        lines.append("メモ: " + " / ".join([str(x) for x in action_lines[:2]]))
    lines.append("")

    # --- AI予想 ---
    lines.append("【AI予想】")
    if pred is None:
        lines.append("予想データ: まだありません")
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
            result_label = "的中" if pred.hit_direction is True else "外れ"
            error_pct = _safe_float(pred.abs_error_pct)

            lines.append(f"{slot_label}: {pred.display_label or pred.pred_direction or '-'}")
            lines.append(f"基準値: {_format_price(reference_close, 2)}")
            lines.append(
                f"予想値: {_format_price(pred_close_value, 2)} "
                f"({_format_signed_percent(pred_close_pct, 2)})"
            )
            lines.append(
                f"実績値: {_format_price(actual_close_value, 2)} "
                f"({_format_signed_percent(actual_close_pct, 2)})"
            )
            lines.append(f"判定: {result_label}")
            if error_pct is not None:
                lines.append(f"誤差: {error_pct:.2f}pt")
        else:
            delta_abs = None
            if reference_close is not None and pred_close_value is not None:
                delta_abs = pred_close_value - reference_close

            reasons = [
                str(pred.display_reason_1 or "").strip(),
                str(pred.display_reason_2 or "").strip(),
                str(pred.display_reason_3 or "").strip(),
            ]
            reasons = [x for x in reasons if x]

            lines.append(f"{slot_label}: {pred.display_label or pred.pred_direction or '-'}")
            lines.append(f"基準値: {_format_price(reference_close, 2)}")
            lines.append(
                f"予想値: {_format_price(pred_close_value, 2)} "
                f"({_format_signed_number(delta_abs, 2)} / {_format_signed_percent(pred_close_pct, 2)})"
            )
            if pred_confidence is not None:
                lines.append(f"確信度: {round(pred_confidence)}%")
            if reasons:
                lines.append("理由: " + " / ".join(reasons))
    lines.append("")

    # --- 市場の偏り ---
    lines.append("【市場の偏り】")
    if bias is None:
        lines.append("データなし")
    else:
        lines.append(f"{_mode_label(bias.mode)}: {bias.summary_title or '-'}")
        if bias.summary_text:
            lines.append(bias.summary_text)

        strong = [str(x) for x in (bias.strong_sectors or [])[:3]]
        weak = [str(x) for x in (bias.weak_sectors or [])[:3]]
        hot = [str(x) for x in (bias.hot_themes or [])[:3]]
        cold = [str(x) for x in (bias.cold_themes or [])[:3]]

        lines.append("強い業種: " + (" / ".join(strong) if strong else "-"))
        lines.append("弱い業種: " + (" / ".join(weak) if weak else "-"))
        lines.append("値上がり偏り: " + (" / ".join(hot) if hot else "-"))
        lines.append("値下がり偏り: " + (" / ".join(cold) if cold else "-"))

    return "\n".join(lines).strip()


class Command(BaseCommand):
    help = "指標ダッシュボードと同じ基準で LINE へ shihyo レポートを送信します。"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="LINE送信せず本文だけ表示する。",
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

        message_text = _build_report_text(latest, pred, bias)

        if options.get("dry_run"):
            self.stdout.write(message_text)
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
                    "type": "text",
                    "text": message_text,
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