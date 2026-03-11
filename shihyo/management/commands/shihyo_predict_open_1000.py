"""
[FILE] shihyo_predict_open_1000.py
[PATH] <project_root>/shihyo/management/commands/shihyo_predict_open_1000.py

このファイルは何？
- 10:00時点の情報から、引けまでの再予想を作って
  ShihyoPreopenPredictionSnapshot(slot=open_1000) に保存するコマンドです。
- 基準値は「対象日の 10:00 時点に最も近い日経平均値」です。
- 市場の偏り(open_1000) と 最新の外部指標スナップショットを使って、
  10:00 → 引け の短期予想を保存します。

今回の修正ポイント：
- 10:00基準値の fallback を厳格化
- MarketIndicatorSnapshot を使う場合は、
  同日 09:55〜10:05 JST の snapshot だけ許可
- それ以外の時刻の snapshot は使わない
- 10:00近辺データが無い日は open_1000 予想を保存しない
"""

from __future__ import annotations

import math
from datetime import date, datetime, time as dt_time
from typing import Any, Optional
from urllib.parse import quote

import requests
from django.core.management.base import BaseCommand, CommandParser
from django.utils import timezone

from shihyo.models import (
    MarketIndicatorSnapshot,
    ShihyoMarketBiasSnapshot,
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


def _clamp(value: float, min_value: float, max_value: float) -> float:
    return max(min_value, min(max_value, value))


def _softmax3(logit_up: float, logit_flat: float, logit_down: float) -> tuple[float, float, float]:
    m = max(logit_up, logit_flat, logit_down)
    e_up = math.exp(logit_up - m)
    e_flat = math.exp(logit_flat - m)
    e_down = math.exp(logit_down - m)
    s = e_up + e_flat + e_down
    if s == 0:
        return 1 / 3, 1 / 3, 1 / 3
    return e_up / s, e_flat / s, e_down / s


def _direction_from_pct(pred_close_pct: float) -> str:
    if pred_close_pct >= 0.25:
        return ShihyoPreopenPredictionSnapshot.PRED_UP
    if pred_close_pct <= -0.25:
        return ShihyoPreopenPredictionSnapshot.PRED_DOWN
    return ShihyoPreopenPredictionSnapshot.PRED_FLAT


def _label_from_direction(direction: str) -> str:
    if direction == ShihyoPreopenPredictionSnapshot.PRED_UP:
        return "10時上昇予想"
    if direction == ShihyoPreopenPredictionSnapshot.PRED_DOWN:
        return "10時下落予想"
    return "10時様子見"


class Command(BaseCommand):
    help = "10:00時点の再予想を作成して ShihyoPreopenPredictionSnapshot(slot=open_1000) に保存します。"

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
            "--model-version",
            type=str,
            default="rule_open1000_v1",
            help="保存するモデルバージョン名。",
        )
        parser.add_argument(
            "--feature-version",
            type=str,
            default="open1000_feature_v1",
            help="保存する特徴量バージョン名。",
        )

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

    def _fetch_intraday_n225_around_1000(self, trade_date: date) -> dict[str, Optional[float]]:
        """
        Yahoo Finance の intraday 履歴から、
        対象日の 10:00 JST に最も近い日経平均値を取る。
        優先順位:
          1) 09:00〜10:00 の範囲で、10:00 以下の最新足
          2) 10:00〜10:05 の範囲で、最も近い直後足
        """
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{quote('^N225', safe='')}"
        data = self._get_json(
            url=url,
            params={
                "interval": "5m",
                "range": "5d",
                "includePrePost": "false",
                "events": "div,splits",
            },
            referer="https://finance.yahoo.com/quote/%5EN225/",
        )
        if not data:
            return {
                "last": None,
                "previous_close": None,
                "pct_vs_prev_close": None,
                "captured_at": None,
                "source": "unavailable",
            }

        try:
            result = (((data or {}).get("chart") or {}).get("result") or [None])[0]
            if not result:
                return {
                    "last": None,
                    "previous_close": None,
                    "pct_vs_prev_close": None,
                    "captured_at": None,
                    "source": "unavailable",
                }

            meta = result.get("meta") or {}
            previous_close = _safe_float(meta.get("previousClose"))

            timestamps = result.get("timestamp") or []
            indicators = result.get("indicators") or {}
            quote_list = indicators.get("quote") or []
            quote0 = quote_list[0] if quote_list else {}
            closes = quote0.get("close") or []

            if not timestamps or not closes:
                return {
                    "last": None,
                    "previous_close": previous_close if _is_valid_number(previous_close) else None,
                    "pct_vs_prev_close": None,
                    "captured_at": None,
                    "source": "unavailable",
                }

            jst = timezone.get_fixed_timezone(9 * 60)

            before_or_equal: list[tuple[datetime, float]] = []
            after_small_window: list[tuple[datetime, float]] = []

            for ts, close_value in zip(timestamps, closes):
                close_f = _safe_float(close_value)
                if not _is_valid_number(close_f):
                    continue

                dt_utc = datetime.fromtimestamp(int(ts), tz=timezone.utc)
                dt_jst = dt_utc.astimezone(jst)

                if dt_jst.date() != trade_date:
                    continue

                t = dt_jst.time()
                if dt_time(9, 0) <= t <= dt_time(10, 0):
                    before_or_equal.append((dt_jst, float(close_f)))
                elif dt_time(10, 0) < t <= dt_time(10, 5):
                    after_small_window.append((dt_jst, float(close_f)))

            selected_dt: Optional[datetime] = None
            selected_value: Optional[float] = None

            if before_or_equal:
                before_or_equal.sort(key=lambda x: x[0])
                selected_dt, selected_value = before_or_equal[-1]
            elif after_small_window:
                after_small_window.sort(key=lambda x: x[0])
                selected_dt, selected_value = after_small_window[0]

            if not _is_valid_number(selected_value):
                return {
                    "last": None,
                    "previous_close": previous_close if _is_valid_number(previous_close) else None,
                    "pct_vs_prev_close": None,
                    "captured_at": None,
                    "source": "unavailable",
                }

            return {
                "last": float(selected_value),
                "previous_close": previous_close if _is_valid_number(previous_close) else None,
                "pct_vs_prev_close": _calc_pct(selected_value, previous_close),
                "captured_at": selected_dt.isoformat() if selected_dt else None,
                "source": "yahoo_intraday_1000",
            }
        except Exception:
            return {
                "last": None,
                "previous_close": None,
                "pct_vs_prev_close": None,
                "captured_at": None,
                "source": "unavailable",
            }

    def _fetch_current_n225_from_snapshot_1000_window(self, trade_date: date) -> dict[str, Optional[float]]:
        """
        最新 snapshot fallback。
        ただし同日 09:55〜10:05 JST の snapshot だけ許可する。
        それ以外の時刻は 10:00基準として不正確なので使わない。
        """
        tz_obj = timezone.get_current_timezone()

        candidates = MarketIndicatorSnapshot.objects.order_by("-created_at")

        for row in candidates:
            local_dt = timezone.localtime(row.created_at, tz_obj)
            if local_dt.date() != trade_date:
                continue

            local_t = local_dt.time()
            if not (dt_time(9, 55) <= local_t <= dt_time(10, 5)):
                continue

            raw_payload = row.raw_payload if isinstance(row.raw_payload, dict) else {}
            nikkei_spot = raw_payload.get("nikkei_spot") or {}

            last_value = _safe_float(nikkei_spot.get("close"))
            previous_close = _safe_float(nikkei_spot.get("previous_close"))

            if (not _is_valid_number(previous_close)) or float(previous_close) <= 0:
                previous_close = _safe_float(row.nikkei_spot_previous_close)

            pct_vs_prev_close = _calc_pct(last_value, previous_close)

            if not _is_valid_number(last_value):
                continue

            return {
                "last": float(last_value),
                "previous_close": previous_close if _is_valid_number(previous_close) else None,
                "pct_vs_prev_close": pct_vs_prev_close if _is_valid_number(pct_vs_prev_close) else None,
                "captured_at": local_dt.isoformat(),
                "source": "market_indicator_snapshot_1000_window",
            }

        return {
            "last": None,
            "previous_close": None,
            "pct_vs_prev_close": None,
            "captured_at": None,
            "source": "unavailable",
        }

    def _resolve_current_n225(self, trade_date: date) -> dict[str, Optional[float]]:
        current_n225 = self._fetch_intraday_n225_around_1000(trade_date)
        if _is_valid_number(current_n225.get("last")):
            return current_n225

        current_n225 = self._fetch_current_n225_from_snapshot_1000_window(trade_date)
        if _is_valid_number(current_n225.get("last")):
            return current_n225

        return {
            "last": None,
            "previous_close": None,
            "pct_vs_prev_close": None,
            "captured_at": None,
            "source": "unavailable",
        }

    def _build_signal_parts(
        self,
        current_n225_pct: float,
        latest_snapshot: Optional[MarketIndicatorSnapshot],
        open_bias: Optional[ShihyoMarketBiasSnapshot],
    ) -> list[dict[str, Any]]:
        parts: list[dict[str, Any]] = []

        intraday_contrib = current_n225_pct * 0.18
        if abs(current_n225_pct) >= 1.2:
            intraday_contrib = current_n225_pct * 0.08
        parts.append({
            "code": "current_n225",
            "contribution": intraday_contrib,
            "raw": current_n225_pct,
        })

        futures_pct = 0.0
        usd_pct = 0.0
        vix_pct = 0.0
        vix_last = 0.0

        if latest_snapshot:
            futures_pct = _safe_float(latest_snapshot.nikkei_futures_change_pct, 0.0) or 0.0
            usd_pct = _safe_float(latest_snapshot.usdjpy_change_pct, 0.0) or 0.0
            vix_pct = _safe_float(latest_snapshot.vix_change_pct, 0.0) or 0.0
            vix_last = _safe_float(latest_snapshot.vix_last, 0.0) or 0.0

        parts.append({
            "code": "futures",
            "contribution": futures_pct * 0.28,
            "raw": futures_pct,
        })
        parts.append({
            "code": "usdjpy",
            "contribution": usd_pct * 0.40,
            "raw": usd_pct,
        })
        parts.append({
            "code": "vix_pct",
            "contribution": (-vix_pct) * 0.05,
            "raw": vix_pct,
        })

        vix_level_contrib = 0.0
        if vix_last >= 25:
            vix_level_contrib = -0.50
        elif vix_last >= 20:
            vix_level_contrib = -0.20
        elif 0 < vix_last < 15:
            vix_level_contrib = 0.12
        parts.append({
            "code": "vix_level",
            "contribution": vix_level_contrib,
            "raw": vix_last,
        })

        tone = open_bias.tone if open_bias else ""
        tone_contrib = 0.0
        if tone == ShihyoMarketBiasSnapshot.TONE_RISK_ON:
            tone_contrib = 0.32
        elif tone == ShihyoMarketBiasSnapshot.TONE_RISK_OFF:
            tone_contrib = -0.32
        parts.append({
            "code": "open_bias_tone",
            "contribution": tone_contrib,
            "raw": tone,
        })

        return parts

    def _reason_text(self, code: str, raw: Any, contribution: float) -> str:
        if code == "current_n225":
            return "10時時点の地合いは継続しやすい" if contribution > 0 else "10時時点の動きはやや失速寄り"
        if code == "futures":
            return "先物が後場の追い風" if contribution > 0 else "先物が後場の重し"
        if code == "usdjpy":
            return "ドル円が後場の追い風" if contribution > 0 else "円高が後場の重し"
        if code == "vix_pct":
            return "VIX低下で後場の警戒後退" if contribution > 0 else "VIX上昇で後場の警戒増加"
        if code == "vix_level":
            return "VIX水準は比較的落ち着き" if contribution > 0 else "VIX水準が高く不安定"
        if code == "open_bias_tone":
            if raw == ShihyoMarketBiasSnapshot.TONE_RISK_ON:
                return "10時時点の市場の偏りは強気寄り"
            if raw == ShihyoMarketBiasSnapshot.TONE_RISK_OFF:
                return "10時時点の市場の偏りは弱気寄り"
            return "10時時点の市場の偏りは中立"
        return "主要指標は中立圏"

    def _build_reasons(self, parts: list[dict[str, Any]]) -> list[str]:
        strong_parts = sorted(parts, key=lambda x: abs(float(x["contribution"])), reverse=True)
        reasons: list[str] = []
        seen: set[str] = set()

        for part in strong_parts:
            contrib = float(part["contribution"])
            if abs(contrib) < 0.03:
                continue
            text = self._reason_text(part["code"], part["raw"], contrib)
            if text and text not in seen:
                reasons.append(text)
                seen.add(text)
            if len(reasons) >= 3:
                break

        if not reasons:
            reasons.append("主要指標は中立圏")

        return reasons[:3]

    def handle(self, *args, **options):
        trade_date_str = (options.get("trade_date") or "").strip()
        model_version = str(options.get("model_version") or "rule_open1000_v1").strip()
        feature_version = str(options.get("feature_version") or "open1000_feature_v1").strip()

        trade_date = timezone.localdate()
        if trade_date_str:
            y, m, d = [int(x) for x in trade_date_str.split("-")]
            trade_date = timezone.datetime(y, m, d).date()

        current_n225 = self._resolve_current_n225(trade_date)
        reference_close = _safe_float(current_n225["last"])
        current_n225_pct = _safe_float(current_n225["pct_vs_prev_close"], 0.0) or 0.0
        current_source = str(current_n225.get("source") or "unavailable")
        captured_at = current_n225.get("captured_at")

        if not _is_valid_number(reference_close) or float(reference_close) <= 0:
            self.stdout.write(
                self.style.ERROR(
                    "10:00時点の日経平均値が取得できません。"
                    " Yahoo intraday 10:00近辺 と 09:55〜10:05 JST の MarketIndicatorSnapshot の両方で取得できませんでした。"
                )
            )
            return

        latest_snapshot = MarketIndicatorSnapshot.objects.order_by("-created_at").first()
        open_bias = (
            ShihyoMarketBiasSnapshot.objects
            .filter(date=trade_date, mode=ShihyoMarketBiasSnapshot.MODE_OPEN_1000)
            .order_by("-updated_at")
            .first()
        )

        parts = self._build_signal_parts(
            current_n225_pct=current_n225_pct,
            latest_snapshot=latest_snapshot,
            open_bias=open_bias,
        )
        raw_signal = sum(float(x["contribution"]) for x in parts)

        pred_close_pct = round(_clamp(raw_signal, -1.50, 1.50), 4)
        pred_close_value = round(reference_close * (1.0 + pred_close_pct / 100.0), 2)

        pred_direction = _direction_from_pct(pred_close_pct)
        display_label = _label_from_direction(pred_direction)

        logit_up = raw_signal * 1.65
        logit_down = -raw_signal * 1.65
        logit_flat = 0.92 - (abs(raw_signal) * 1.40)

        up_p, flat_p, down_p = _softmax3(logit_up, logit_flat, logit_down)

        pred_up_prob = round(up_p * 100.0, 2)
        pred_flat_prob = round(flat_p * 100.0, 2)
        pred_down_prob = round(down_p * 100.0, 2)
        pred_confidence = round(max(pred_up_prob, pred_flat_prob, pred_down_prob), 2)

        reasons = self._build_reasons(parts)
        predicted_at = timezone.now()

        payload = {
            "predicted_at": predicted_at,
            "feature_version": feature_version,
            "reference_close_n225": reference_close,

            "pred_direction": pred_direction,
            "pred_up_prob": pred_up_prob,
            "pred_down_prob": pred_down_prob,
            "pred_flat_prob": pred_flat_prob,

            "pred_close_pct": pred_close_pct,
            "pred_close_value": pred_close_value,
            "pred_confidence": pred_confidence,

            "display_label": display_label,
            "display_reason_1": reasons[0] if len(reasons) >= 1 else "",
            "display_reason_2": reasons[1] if len(reasons) >= 2 else "",
            "display_reason_3": reasons[2] if len(reasons) >= 3 else "",

            "feature_snapshot": None,
            "raw_prediction_payload": {
                "trade_date": trade_date.isoformat(),
                "slot": ShihyoPreopenPredictionSnapshot.SLOT_OPEN_1000,
                "model_version": model_version,
                "feature_version": feature_version,
                "reference_close_n225": reference_close,
                "current_n225_pct_vs_prev_close": current_n225_pct,
                "previous_close_n225": current_n225.get("previous_close"),
                "current_n225_source": current_source,
                "current_n225_captured_at": captured_at,
                "raw_signal": round(raw_signal, 6),
                "open_bias_id": open_bias.id if open_bias else None,
                "open_bias_tone": open_bias.tone if open_bias else "",
                "latest_snapshot_id": latest_snapshot.id if latest_snapshot else None,
                "signal_parts": [
                    {
                        "code": str(x["code"]),
                        "contribution": round(float(x["contribution"]), 6),
                        "raw": x["raw"],
                    }
                    for x in parts
                ],
                "reasons": reasons,
            },
        }

        obj, created = ShihyoPreopenPredictionSnapshot.objects.update_or_create(
            trade_date=trade_date,
            slot=ShihyoPreopenPredictionSnapshot.SLOT_OPEN_1000,
            model_version=model_version,
            defaults=payload,
        )

        action = "created" if created else "updated"

        self.stdout.write(
            self.style.SUCCESS(
                f"[shihyo_predict_open_1000] {action} "
                f"trade_date={obj.trade_date} "
                f"slot={obj.slot} "
                f"model_version={obj.model_version} "
                f"direction={obj.pred_direction} "
                f"close_pct={obj.pred_close_pct} "
                f"close_value={obj.pred_close_value} "
                f"confidence={obj.pred_confidence} "
                f"source={current_source} "
                f"captured_at={captured_at}"
            )
        )