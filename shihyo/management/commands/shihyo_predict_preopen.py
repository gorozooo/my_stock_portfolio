"""
[FILE] shihyo_predict_preopen.py
[PATH] <project_root>/shihyo/management/commands/shihyo_predict_preopen.py

このファイルは何？
- 朝7:00モデル用の特徴量1行から、ルールベース予測を作って
  ShihyoPreopenPredictionSnapshot に保存するコマンドです。
- MVP版の model_version は rule_v1 を使います。
- 基準値は必ず prev_close_n225（前営業日終値）です。
- 保存するものは、
  1) 方向
  2) 上昇/下落/様子見 確率
  3) 予想騰落率
  4) 予想値
  5) 信頼度
  6) 表示用ラベルと理由
です。
"""

from __future__ import annotations

import math
from typing import Any, Optional

from django.core.management.base import BaseCommand, CommandParser
from django.utils import timezone

from shihyo.models import (
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
    if pred_close_pct >= 0.35:
        return ShihyoPreopenPredictionSnapshot.PRED_UP
    if pred_close_pct <= -0.35:
        return ShihyoPreopenPredictionSnapshot.PRED_DOWN
    return ShihyoPreopenPredictionSnapshot.PRED_FLAT


def _label_from_direction(direction: str) -> str:
    if direction == ShihyoPreopenPredictionSnapshot.PRED_UP:
        return "上昇予想"
    if direction == ShihyoPreopenPredictionSnapshot.PRED_DOWN:
        return "下落予想"
    return "様子見"


def _build_signal_parts(feature: ShihyoPreopenFeatureSnapshot) -> list[dict[str, Any]]:
    """
    特徴量から rule_v1 の寄与を作る。
    返り値の各要素:
      {
        "code": "...",
        "contribution": float,
        "raw": float|str|bool|None,
      }
    """
    parts: list[dict[str, Any]] = []

    futures_pct = _safe_float(feature.nikkei_futures_pct_vs_prev_close_n225, 0.0) or 0.0
    parts.append({
        "code": "futures",
        "contribution": futures_pct * 0.90,
        "raw": futures_pct,
    })

    usd_pct = _safe_float(feature.usdjpy_pct_1d, 0.0) or 0.0
    parts.append({
        "code": "usdjpy",
        "contribution": usd_pct * 1.10,
        "raw": usd_pct,
    })

    vix_pct = _safe_float(feature.vix_pct_1d, 0.0) or 0.0
    parts.append({
        "code": "vix_pct",
        "contribution": (-vix_pct) * 0.08,
        "raw": vix_pct,
    })

    vix_last = _safe_float(feature.vix_last, 0.0) or 0.0
    vix_level_contrib = 0.0
    if vix_last >= 25:
        vix_level_contrib = -0.80
    elif vix_last >= 20:
        vix_level_contrib = -0.35
    elif 0 < vix_last < 15:
        vix_level_contrib = 0.25
    parts.append({
        "code": "vix_level",
        "contribution": vix_level_contrib,
        "raw": vix_last,
    })

    sp500_pct = _safe_float(feature.sp500_pct_1d, 0.0) or 0.0
    parts.append({
        "code": "sp500",
        "contribution": sp500_pct * 0.30,
        "raw": sp500_pct,
    })

    nasdaq_pct = _safe_float(feature.nasdaq100_pct_1d, 0.0) or 0.0
    parts.append({
        "code": "nasdaq100",
        "contribution": nasdaq_pct * 0.22,
        "raw": nasdaq_pct,
    })

    sox_pct = _safe_float(feature.sox_pct_1d, 0.0) or 0.0
    parts.append({
        "code": "sox",
        "contribution": sox_pct * 0.15,
        "raw": sox_pct,
    })

    bias_tone = (feature.prev_market_bias_tone or "").strip()
    bias_contrib = 0.0
    if bias_tone == "risk_on":
        bias_contrib = 0.35
    elif bias_tone == "risk_off":
        bias_contrib = -0.35
    parts.append({
        "code": "bias_tone",
        "contribution": bias_contrib,
        "raw": bias_tone,
    })

    risk_score = _safe_float(feature.risk_score_legacy, 50.0) or 50.0
    risk_contrib = -((risk_score - 50.0) / 50.0) * 0.60
    parts.append({
        "code": "risk_legacy",
        "contribution": risk_contrib,
        "raw": risk_score,
    })

    if feature.is_sq_week:
        gap_pts = abs(_safe_float(feature.nikkei_futures_gap_pts, 0.0) or 0.0)
        sq_contrib = -0.15 if gap_pts >= 250 else -0.05
        parts.append({
            "code": "sq_week",
            "contribution": sq_contrib,
            "raw": True,
        })

    if feature.is_major_holiday_adjacent:
        parts.append({
            "code": "holiday_adjacent",
            "contribution": -0.08,
            "raw": True,
        })

    return parts


def _reason_text(code: str, raw: Any, contribution: float) -> str:
    if code == "futures":
        return "日経先物が追い風" if contribution > 0 else "日経先物が重し"
    if code == "usdjpy":
        return "ドル円が追い風" if contribution > 0 else "円高が重し"
    if code == "vix_pct":
        return "VIX低下で警戒後退" if contribution > 0 else "VIX上昇で警戒増加"
    if code == "vix_level":
        return "VIX水準は比較的落ち着き" if contribution > 0 else "VIX水準が高く不安定"
    if code == "sp500":
        return "米国株が追い風" if contribution > 0 else "米国株が重し"
    if code == "nasdaq100":
        return "NASDAQが追い風" if contribution > 0 else "NASDAQが重し"
    if code == "sox":
        return "半導体地合いが追い風" if contribution > 0 else "半導体地合いが重し"
    if code == "bias_tone":
        if raw == "risk_on":
            return "前日の市場の偏りは強気寄り"
        if raw == "risk_off":
            return "前日の市場の偏りは弱気寄り"
        return "前日の市場の偏りは中立"
    if code == "risk_legacy":
        return "旧リスク判定は攻め寄り" if contribution > 0 else "旧リスク判定は守り寄り"
    if code == "sq_week":
        return "SQ週で需給のブレに注意"
    if code == "holiday_adjacent":
        return "連休前後でブレやすい地合い"
    return "主要指標は中立圏"


def _build_reasons(parts: list[dict[str, Any]]) -> list[str]:
    strong_parts = sorted(parts, key=lambda x: abs(float(x["contribution"])), reverse=True)

    reasons: list[str] = []
    seen: set[str] = set()

    for part in strong_parts:
        contrib = float(part["contribution"])
        if abs(contrib) < 0.05:
            continue
        text = _reason_text(part["code"], part["raw"], contrib)
        if text and text not in seen:
            reasons.append(text)
            seen.add(text)
        if len(reasons) >= 3:
            break

    if not reasons:
        reasons.append("主要指標は中立圏")

    return reasons[:3]


class Command(BaseCommand):
    help = "朝7:00モデルの特徴量1行から rule_v1 予測を作成し、ShihyoPreopenPredictionSnapshot に保存します。"

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
            default="rule_v1",
            help="保存するモデルバージョン名。",
        )

    def handle(self, *args, **options):
        trade_date_str = (options.get("trade_date") or "").strip()
        model_version = str(options.get("model_version") or "rule_v1").strip()

        trade_date = timezone.localdate()
        if trade_date_str:
            y, m, d = [int(x) for x in trade_date_str.split("-")]
            trade_date = timezone.datetime(y, m, d).date()

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
        if not reference_close or reference_close <= 0:
            self.stdout.write(
                self.style.ERROR(
                    "prev_close_n225 が入っていません。"
                    " 予測の基準値がないため保存できません。"
                )
            )
            return

        parts = _build_signal_parts(feature)
        raw_signal = sum(float(x["contribution"]) for x in parts)
        pred_close_pct = round(_clamp(raw_signal, -2.80, 2.80), 4)
        pred_close_value = round(reference_close * (1.0 + pred_close_pct / 100.0), 2)

        pred_direction = _direction_from_pct(pred_close_pct)
        display_label = _label_from_direction(pred_direction)

        # --- 確率化 ---
        logit_up = raw_signal * 1.35
        logit_down = -raw_signal * 1.35
        logit_flat = 0.85 - (abs(raw_signal) * 1.65)

        up_p, flat_p, down_p = _softmax3(logit_up, logit_flat, logit_down)

        pred_up_prob = round(up_p * 100.0, 2)
        pred_flat_prob = round(flat_p * 100.0, 2)
        pred_down_prob = round(down_p * 100.0, 2)

        pred_confidence = round(max(pred_up_prob, pred_flat_prob, pred_down_prob), 2)

        reasons = _build_reasons(parts)
        predicted_at = timezone.now()

        payload = {
            "predicted_at": predicted_at,
            "feature_version": feature.feature_version,
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

            "feature_snapshot": feature,
            "raw_prediction_payload": {
                "trade_date": trade_date.isoformat(),
                "slot": feature.slot,
                "model_version": model_version,
                "feature_version": feature.feature_version,
                "reference_close_n225": reference_close,
                "raw_signal": round(raw_signal, 6),
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
            slot=ShihyoPreopenPredictionSnapshot.SLOT_PREOPEN_0700,
            model_version=model_version,
            defaults=payload,
        )

        action = "created" if created else "updated"

        self.stdout.write(
            self.style.SUCCESS(
                f"[shihyo_predict_preopen] {action} "
                f"trade_date={obj.trade_date} "
                f"slot={obj.slot} "
                f"model_version={obj.model_version} "
                f"direction={obj.pred_direction} "
                f"close_pct={obj.pred_close_pct} "
                f"close_value={obj.pred_close_value} "
                f"confidence={obj.pred_confidence}"
            )
        )