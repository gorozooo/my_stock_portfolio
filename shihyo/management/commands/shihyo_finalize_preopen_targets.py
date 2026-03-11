"""
[FILE] shihyo_finalize_preopen_targets.py
[PATH] <project_root>/shihyo/management/commands/shihyo_finalize_preopen_targets.py

このファイルは何？
- 朝7:00モデル用の正解ラベルを、引け後に
  ShihyoPreopenFeatureSnapshot / ShihyoPreopenPredictionSnapshot の両方へ保存するコマンドです。
- 対象日の実際の日経平均終値を使って、
  1) target_close_value / target_close_pct / target_direction_3
  2) actual_close_value / actual_close_pct / actual_direction_3
  3) hit_direction / abs_error_pct
  を埋めます。

今回の方針：
- まず Yahoo Finance の ^N225 履歴日足から対象日の終値を取得
- 取れない場合だけ、当日引け後の MarketIndicatorSnapshot を fallback に使う
- 基準は必ず prev_close_n225（前営業日終値）
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Optional
from urllib.parse import quote

import requests
from django.core.management.base import BaseCommand, CommandParser
from django.utils import timezone

from shihyo.models import (
    MarketIndicatorSnapshot,
    ShihyoPreopenFeatureSnapshot,
    ShihyoPreopenPredictionSnapshot,
)


def _safe_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _is_valid_number(value: Optional[float]) -> bool:
    if value is None:
        return False
    try:
        x = float(value)
    except (TypeError, ValueError):
        return False
    return not (x != x or x in (float("inf"), float("-inf")))


def _calc_pct(last_value: Optional[float], prev_close_value: Optional[float]) -> Optional[float]:
    if not _is_valid_number(last_value) or not _is_valid_number(prev_close_value):
        return None
    last_f = float(last_value)
    prev_f = float(prev_close_value)
    if prev_f == 0:
        return None
    return ((last_f / prev_f) - 1.0) * 100.0


def _direction_from_pct(value: Optional[float], threshold: float) -> str:
    if not _is_valid_number(value):
        return ""
    x = float(value)
    if x >= threshold:
        return ShihyoPreopenFeatureSnapshot.TARGET_UP
    if x <= -threshold:
        return ShihyoPreopenFeatureSnapshot.TARGET_DOWN
    return ShihyoPreopenFeatureSnapshot.TARGET_FLAT


def _parse_date(value: str) -> date:
    y, m, d = [int(x) for x in value.split("-")]
    return date(y, m, d)


class Command(BaseCommand):
    help = "朝7:00モデルの正解ラベルを引け後に確定して保存します。"

    USER_AGENT = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/123.0.0.0 Safari/537.36"
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--trade-date",
            type=str,
            default="",
            help="対象営業日(YYYY-MM-DD)。未指定ならJSTの今日。",
        )
        parser.add_argument(
            "--direction-threshold",
            type=float,
            default=0.35,
            help="方向ラベルの閾値(%%)。既定 0.35。",
        )

    # =========================
    # 共通HTTP
    # =========================
    def _get_json(self, url: str, params: dict, referer: str) -> Optional[dict]:
        headers = {
            "User-Agent": self.USER_AGENT,
            "Accept": "application/json,text/plain,*/*",
            "Referer": referer,
        }
        try:
            r = requests.get(url, params=params, headers=headers, timeout=10)
            r.raise_for_status()
            return r.json()
        except Exception:
            return None

    # =========================
    # Yahoo から対象日の ^N225 終値を取得
    # =========================
    def _fetch_n225_close_from_yahoo_history(self, trade_date: date) -> tuple[Optional[float], str]:
        """
        Yahoo Finance の chart API 日足から target_date の終値を取る。
        """
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{quote('^N225', safe='')}"
        data = self._get_json(
            url=url,
            params={
                "interval": "1d",
                "range": "2mo",
                "includePrePost": "false",
                "events": "div,splits",
            },
            referer="https://finance.yahoo.com/quote/%5EN225/",
        )
        if not data:
            return None, "unavailable"

        try:
            result = (((data or {}).get("chart") or {}).get("result") or [None])[0]
            if not result:
                return None, "unavailable"

            meta = result.get("meta") or {}
            exchange_tz_name = str(meta.get("exchangeTimezoneName") or "Asia/Tokyo")

            timestamps = result.get("timestamp") or []
            indicators = result.get("indicators") or {}
            quote_list = indicators.get("quote") or []
            quote0 = quote_list[0] if quote_list else {}
            closes = quote0.get("close") or []

            if not timestamps or not closes:
                return None, "unavailable"

            tz_obj = timezone.get_fixed_timezone(9 * 60)
            if exchange_tz_name == "Asia/Tokyo":
                tz_obj = timezone.get_fixed_timezone(9 * 60)

            for ts, close_value in zip(timestamps, closes):
                close_f = _safe_float(close_value)
                if not _is_valid_number(close_f):
                    continue
                dt_utc = datetime.fromtimestamp(int(ts), tz=timezone.utc)
                dt_local = dt_utc.astimezone(tz_obj)
                if dt_local.date() == trade_date:
                    return float(close_f), "yahoo_history"

            return None, "unavailable"
        except Exception:
            return None, "unavailable"

    # =========================
    # fallback: 当日引け後の snapshot
    # =========================
    def _fetch_n225_close_from_snapshot(self, trade_date: date) -> tuple[Optional[float], str]:
        """
        当日15:00 JST以降の MarketIndicatorSnapshot の raw_payload から
        nikkei_spot.close を拾う。
        """
        tz_obj = timezone.get_current_timezone()

        candidates = (
            MarketIndicatorSnapshot.objects
            .order_by("-created_at")
        )

        for row in candidates:
            local_dt = timezone.localtime(row.created_at, tz_obj)
            if local_dt.date() != trade_date:
                continue
            if local_dt.hour < 15:
                continue

            raw_payload = row.raw_payload if isinstance(row.raw_payload, dict) else {}
            nikkei_spot = raw_payload.get("nikkei_spot") or {}
            close_value = _safe_float(nikkei_spot.get("close"))
            if _is_valid_number(close_value) and float(close_value) > 0:
                return float(close_value), "market_indicator_snapshot"

        return None, "unavailable"

    def _resolve_actual_close(self, trade_date: date) -> tuple[Optional[float], str]:
        actual_close, source = self._fetch_n225_close_from_yahoo_history(trade_date)
        if _is_valid_number(actual_close):
            return float(actual_close), source

        actual_close, source = self._fetch_n225_close_from_snapshot(trade_date)
        if _is_valid_number(actual_close):
            return float(actual_close), source

        return None, "unavailable"

    def handle(self, *args, **options):
        trade_date_str = (options.get("trade_date") or "").strip()
        threshold = float(options.get("direction_threshold") or 0.35)

        trade_date = timezone.localdate()
        if trade_date_str:
            trade_date = _parse_date(trade_date_str)

        feature = (
            ShihyoPreopenFeatureSnapshot.objects
            .filter(
                trade_date=trade_date,
                slot=ShihyoPreopenFeatureSnapshot.SLOT_PREOPEN_0700,
            )
            .order_by("-updated_at")
            .first()
        )

        if not feature:
            self.stdout.write(
                self.style.ERROR(
                    "対象日の ShihyoPreopenFeatureSnapshot がありません。"
                    " 先に `python manage.py shihyo_build_preopen_features` を実行してください。"
                )
            )
            return

        reference_close = _safe_float(feature.prev_close_n225)
        if not _is_valid_number(reference_close) or float(reference_close) <= 0:
            self.stdout.write(
                self.style.ERROR(
                    "prev_close_n225 が入っていません。"
                    " 正解ラベルの基準値がないため保存できません。"
                )
            )
            return

        actual_close, actual_source = self._resolve_actual_close(trade_date)
        if not _is_valid_number(actual_close) or float(actual_close) <= 0:
            self.stdout.write(
                self.style.ERROR(
                    f"対象日 {trade_date} の実際の終値が取得できませんでした。"
                    " Yahoo履歴と MarketIndicatorSnapshot の両方で見つかりません。"
                )
            )
            return

        actual_close_pct = _calc_pct(actual_close, reference_close)
        actual_direction = _direction_from_pct(actual_close_pct, threshold)

        # --- FeatureSnapshot 更新 ---
        feature.target_close_value = float(actual_close)
        feature.target_close_pct = float(actual_close_pct) if _is_valid_number(actual_close_pct) else None
        feature.target_direction_3 = actual_direction
        feature.is_target_fixed = True

        source_payload = feature.source_payload if isinstance(feature.source_payload, dict) else {}
        source_payload["target_finalize"] = {
            "actual_close_source": actual_source,
            "actual_close_value": float(actual_close),
            "actual_close_pct": float(actual_close_pct) if _is_valid_number(actual_close_pct) else None,
            "actual_direction_3": actual_direction,
            "threshold": threshold,
            "finalized_at": timezone.now().isoformat(),
        }
        feature.source_payload = source_payload
        feature.save()

        # --- PredictionSnapshot 更新 ---
        predictions = list(
            ShihyoPreopenPredictionSnapshot.objects.filter(
                trade_date=trade_date,
                slot=ShihyoPreopenPredictionSnapshot.SLOT_PREOPEN_0700,
            ).order_by("model_version")
        )

        updated_predictions = 0
        for pred in predictions:
            pred.actual_close_value = float(actual_close)
            pred.actual_close_pct = float(actual_close_pct) if _is_valid_number(actual_close_pct) else None
            pred.actual_direction_3 = actual_direction
            pred.hit_direction = (pred.pred_direction == actual_direction) if pred.pred_direction else None

            pred_pct = _safe_float(pred.pred_close_pct)
            if _is_valid_number(pred_pct) and _is_valid_number(actual_close_pct):
                pred.abs_error_pct = abs(float(pred_pct) - float(actual_close_pct))
            else:
                pred.abs_error_pct = None

            pred.evaluated_at = timezone.now()

            raw_payload = pred.raw_prediction_payload if isinstance(pred.raw_prediction_payload, dict) else {}
            raw_payload["evaluation"] = {
                "actual_close_source": actual_source,
                "actual_close_value": float(actual_close),
                "actual_close_pct": float(actual_close_pct) if _is_valid_number(actual_close_pct) else None,
                "actual_direction_3": actual_direction,
                "hit_direction": pred.hit_direction,
                "abs_error_pct": pred.abs_error_pct,
                "evaluated_at": pred.evaluated_at.isoformat() if pred.evaluated_at else None,
                "threshold": threshold,
            }
            pred.raw_prediction_payload = raw_payload
            pred.save()
            updated_predictions += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"[shihyo_finalize_preopen_targets] "
                f"trade_date={trade_date} "
                f"actual_close={actual_close} "
                f"actual_close_pct={actual_close_pct} "
                f"actual_direction={actual_direction} "
                f"source={actual_source} "
                f"feature_fixed=1 "
                f"predictions_updated={updated_predictions}"
            )
        )