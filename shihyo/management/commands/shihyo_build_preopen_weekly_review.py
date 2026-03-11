"""
[FILE] shihyo_build_preopen_weekly_review.py
[PATH] <project_root>/shihyo/management/commands/shihyo_build_preopen_weekly_review.py

このファイルは何？
- 朝7:00モデルの週次レビューを集計して
  ShihyoPreopenWeeklyReview に保存するコマンドです。
- 直近20営業日を基本窓として、
  1) 全体方向精度
  2) 高信頼度方向精度
  3) 大外し率
  4) 条件別弱点精度
  5) 最大弱点
  6) 次の追加候補1件
  を保存します。

今回のMVP方針：
- 条件別弱点はまず4種類だけを見る
  - sq_week
  - vix_spike
  - futures_gap
  - fx_shock
- 最大弱点が出た時だけ proposal_ready
- データ不足なら insufficient_data
- 弱点がなければ stable
"""

from __future__ import annotations

from datetime import date, timedelta
from statistics import median
from typing import Any, Optional

from django.core.management.base import BaseCommand, CommandParser
from django.utils import timezone

from shihyo.models import (
    ShihyoPreopenPredictionSnapshot,
    ShihyoPreopenWeeklyReview,
)


def _safe_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _accuracy(items: list[ShihyoPreopenPredictionSnapshot]) -> Optional[float]:
    if not items:
        return None
    hits = sum(1 for x in items if x.hit_direction is True)
    return hits / len(items)


def _median_or_none(values: list[float]) -> Optional[float]:
    if not values:
        return None
    return float(median(values))


def _week_start(target_date: date) -> date:
    return target_date - timedelta(days=target_date.weekday())


def _week_end(target_date: date) -> date:
    return _week_start(target_date) + timedelta(days=4)


def _is_vix_spike(pred: ShihyoPreopenPredictionSnapshot) -> bool:
    f = pred.feature_snapshot
    if not f:
        return False
    vix_pct = _safe_float(f.vix_pct_1d, 0.0) or 0.0
    vix_last = _safe_float(f.vix_last, 0.0) or 0.0
    return (vix_pct >= 8.0) or (vix_last >= 25.0)


def _is_futures_gap(pred: ShihyoPreopenPredictionSnapshot) -> bool:
    f = pred.feature_snapshot
    if not f:
        return False
    gap_pts = abs(_safe_float(f.nikkei_futures_gap_pts, 0.0) or 0.0)
    gap_pct = abs(_safe_float(f.nikkei_futures_pct_vs_prev_close_n225, 0.0) or 0.0)
    return (gap_pts >= 400.0) or (gap_pct >= 0.8)


def _is_fx_shock(pred: ShihyoPreopenPredictionSnapshot) -> bool:
    f = pred.feature_snapshot
    if not f:
        return False
    fx_pct = abs(_safe_float(f.usdjpy_pct_1d, 0.0) or 0.0)
    return fx_pct >= 1.0


def _is_sq_week(pred: ShihyoPreopenPredictionSnapshot) -> bool:
    f = pred.feature_snapshot
    if not f:
        return False
    return bool(f.is_sq_week)


def _proposal_from_weakness(weakness_code: str) -> dict[str, str]:
    mapping = {
        "sq_week": {
            "proposal_code": "add_sq_pack_v1",
            "proposal_label": "SQ関連特徴を追加",
            "proposal_feature_group": "sq_pack",
            "proposal_reason": "SQ週の方向精度が全体より大きく低下しているため",
            "proposal_cost_level": "low",
        },
        "vix_spike": {
            "proposal_code": "add_vix_pack_v1",
            "proposal_label": "VIX強化特徴を追加",
            "proposal_feature_group": "vix_pack",
            "proposal_reason": "VIX急騰日の方向精度が全体より大きく低下しているため",
            "proposal_cost_level": "low",
        },
        "futures_gap": {
            "proposal_code": "add_gap_pack_v1",
            "proposal_label": "先物ギャップ特徴を追加",
            "proposal_feature_group": "gap_pack",
            "proposal_reason": "先物ギャップ日の方向精度が全体より大きく低下しているため",
            "proposal_cost_level": "low",
        },
        "fx_shock": {
            "proposal_code": "add_fx_pack_v1",
            "proposal_label": "為替急変特徴を追加",
            "proposal_feature_group": "fx_pack",
            "proposal_reason": "為替急変日の方向精度が全体より大きく低下しているため",
            "proposal_cost_level": "low",
        },
    }
    return mapping.get(
        weakness_code,
        {
            "proposal_code": "",
            "proposal_label": "",
            "proposal_feature_group": "",
            "proposal_reason": "",
            "proposal_cost_level": "",
        },
    )


class Command(BaseCommand):
    help = "朝7:00モデルの週次レビューを集計して保存します。"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--week-date",
            type=str,
            default="",
            help="この日を含む週でレビューを作る(YYYY-MM-DD)。未指定ならJSTの今日。",
        )
        parser.add_argument(
            "--lookback",
            type=int,
            default=20,
            help="集計に使う直近営業日数。既定20。",
        )
        parser.add_argument(
            "--min-samples",
            type=int,
            default=20,
            help="レビュー確定に必要な最小サンプル数。既定20。",
        )
        parser.add_argument(
            "--high-conf-threshold",
            type=float,
            default=60.0,
            help="高信頼度とみなす pred_confidence の閾値。既定60。",
        )
        parser.add_argument(
            "--model-version",
            type=str,
            default="rule_v1",
            help="対象モデルバージョン。既定 rule_v1。",
        )
        parser.add_argument(
            "--feature-version",
            type=str,
            default="preopen_feature_v1",
            help="対象特徴量バージョン。既定 preopen_feature_v1。",
        )

    def handle(self, *args, **options):
        week_date_str = (options.get("week_date") or "").strip()
        lookback = int(options.get("lookback") or 20)
        min_samples = int(options.get("min_samples") or 20)
        high_conf_threshold = float(options.get("high_conf_threshold") or 60.0)
        model_version = str(options.get("model_version") or "rule_v1").strip()
        feature_version = str(options.get("feature_version") or "preopen_feature_v1").strip()

        review_date = timezone.localdate()
        if week_date_str:
            y, m, d = [int(x) for x in week_date_str.split("-")]
            review_date = date(y, m, d)

        week_start_date = _week_start(review_date)
        week_end_date = _week_end(review_date)

        qs = (
            ShihyoPreopenPredictionSnapshot.objects
            .select_related("feature_snapshot")
            .filter(
                slot=ShihyoPreopenPredictionSnapshot.SLOT_PREOPEN_0700,
                model_version=model_version,
                feature_version=feature_version,
                trade_date__lte=week_end_date,
            )
            .exclude(actual_direction_3="")
            .order_by("-trade_date", "-predicted_at")
        )

        predictions = list(qs[:lookback])
        sample_count = len(predictions)

        high_conf_predictions = [
            x for x in predictions
            if (_safe_float(x.pred_confidence, 0.0) or 0.0) >= high_conf_threshold
        ]

        direction_accuracy = _accuracy(predictions)
        high_conf_direction_accuracy = _accuracy(high_conf_predictions)

        abs_error_values = [
            float(x.abs_error_pct)
            for x in predictions
            if _safe_float(x.abs_error_pct) is not None
        ]
        mean_abs_error_pct = (
            sum(abs_error_values) / len(abs_error_values)
            if abs_error_values else None
        )
        median_abs_error_pct = _median_or_none(abs_error_values)

        big_miss_values = [x for x in abs_error_values if x >= 2.0]
        big_miss_count = len(big_miss_values)
        big_miss_ratio = (
            big_miss_count / len(abs_error_values)
            if abs_error_values else None
        )

        sq_week_items = [x for x in predictions if _is_sq_week(x)]
        vix_spike_items = [x for x in predictions if _is_vix_spike(x)]
        futures_gap_items = [x for x in predictions if _is_futures_gap(x)]
        fx_shock_items = [x for x in predictions if _is_fx_shock(x)]

        sq_week_accuracy = _accuracy(sq_week_items)
        vix_spike_accuracy = _accuracy(vix_spike_items)
        futures_gap_accuracy = _accuracy(futures_gap_items)
        fx_shock_accuracy = _accuracy(fx_shock_items)

        high_conf_sq = [
            x for x in sq_week_items
            if (_safe_float(x.pred_confidence, 0.0) or 0.0) >= high_conf_threshold
        ]
        high_conf_vix = [
            x for x in vix_spike_items
            if (_safe_float(x.pred_confidence, 0.0) or 0.0) >= high_conf_threshold
        ]
        high_conf_gap = [
            x for x in futures_gap_items
            if (_safe_float(x.pred_confidence, 0.0) or 0.0) >= high_conf_threshold
        ]
        high_conf_fx = [
            x for x in fx_shock_items
            if (_safe_float(x.pred_confidence, 0.0) or 0.0) >= high_conf_threshold
        ]

        weakness_candidates: list[dict[str, Any]] = []
        if direction_accuracy is not None:
            rules = [
                {
                    "code": "sq_week",
                    "label": "SQ週に弱い",
                    "items": sq_week_items,
                    "accuracy": sq_week_accuracy,
                    "high_conf_accuracy": _accuracy(high_conf_sq),
                },
                {
                    "code": "vix_spike",
                    "label": "VIX急騰日に弱い",
                    "items": vix_spike_items,
                    "accuracy": vix_spike_accuracy,
                    "high_conf_accuracy": _accuracy(high_conf_vix),
                },
                {
                    "code": "futures_gap",
                    "label": "先物ギャップ日に弱い",
                    "items": futures_gap_items,
                    "accuracy": futures_gap_accuracy,
                    "high_conf_accuracy": _accuracy(high_conf_gap),
                },
                {
                    "code": "fx_shock",
                    "label": "為替急変日に弱い",
                    "items": fx_shock_items,
                    "accuracy": fx_shock_accuracy,
                    "high_conf_accuracy": _accuracy(high_conf_fx),
                },
            ]

            for rule in rules:
                count = len(rule["items"])
                acc = rule["accuracy"]
                if count < 5 or acc is None:
                    continue

                gap_vs_overall = float(acc) - float(direction_accuracy)

                hc_gap = None
                if (
                    high_conf_direction_accuracy is not None
                    and rule["high_conf_accuracy"] is not None
                ):
                    hc_gap = float(rule["high_conf_accuracy"]) - float(high_conf_direction_accuracy)

                qualifies = gap_vs_overall <= -0.08
                if hc_gap is not None:
                    qualifies = qualifies and (hc_gap <= -0.05)

                if qualifies:
                    weakness_candidates.append({
                        "code": rule["code"],
                        "label": rule["label"],
                        "count": count,
                        "accuracy": float(acc),
                        "gap_vs_overall": float(gap_vs_overall),
                        "high_conf_gap_vs_overall": hc_gap,
                    })

        weakness_candidates = sorted(
            weakness_candidates,
            key=lambda x: (x["gap_vs_overall"], -x["count"]),
        )

        weakness = weakness_candidates[0] if weakness_candidates else None

        status = ShihyoPreopenWeeklyReview.STATUS_STABLE
        if sample_count < min_samples:
            status = ShihyoPreopenWeeklyReview.STATUS_INSUFFICIENT_DATA
        elif weakness:
            status = ShihyoPreopenWeeklyReview.STATUS_PROPOSAL_READY

        proposal = _proposal_from_weakness(weakness["code"]) if weakness else {
            "proposal_code": "",
            "proposal_label": "",
            "proposal_feature_group": "",
            "proposal_reason": "",
            "proposal_cost_level": "",
        }

        review_payload = {
            "review_date": review_date.isoformat(),
            "lookback": lookback,
            "min_samples": min_samples,
            "high_conf_threshold": high_conf_threshold,
            "trade_dates": [x.trade_date.isoformat() for x in predictions],
            "weakness_candidates": weakness_candidates,
            "condition_counts": {
                "sq_week": len(sq_week_items),
                "vix_spike": len(vix_spike_items),
                "futures_gap": len(futures_gap_items),
                "fx_shock": len(fx_shock_items),
            },
        }

        defaults = {
            "sample_count": sample_count,
            "direction_accuracy": direction_accuracy,
            "high_conf_sample_count": len(high_conf_predictions),
            "high_conf_direction_accuracy": high_conf_direction_accuracy,
            "mean_abs_error_pct": mean_abs_error_pct,
            "median_abs_error_pct": median_abs_error_pct,
            "big_miss_count": big_miss_count,
            "big_miss_ratio": big_miss_ratio,

            "sq_week_count": len(sq_week_items),
            "sq_week_accuracy": sq_week_accuracy,
            "vix_spike_count": len(vix_spike_items),
            "vix_spike_accuracy": vix_spike_accuracy,
            "futures_gap_count": len(futures_gap_items),
            "futures_gap_accuracy": futures_gap_accuracy,
            "fx_shock_count": len(fx_shock_items),
            "fx_shock_accuracy": fx_shock_accuracy,

            "weakness_code": weakness["code"] if weakness else "",
            "weakness_label": weakness["label"] if weakness else "",
            "weakness_sample_count": weakness["count"] if weakness else 0,
            "weakness_accuracy": weakness["accuracy"] if weakness else None,
            "weakness_gap_vs_overall": weakness["gap_vs_overall"] if weakness else None,
            "weakness_high_conf_gap_vs_overall": (
                weakness["high_conf_gap_vs_overall"] if weakness else None
            ),

            "proposal_code": proposal["proposal_code"],
            "proposal_label": proposal["proposal_label"],
            "proposal_feature_group": proposal["proposal_feature_group"],
            "proposal_reason": proposal["proposal_reason"],
            "proposal_priority": 1 if weakness else 0,
            "proposal_cost_level": proposal["proposal_cost_level"],

            "status": status,

            "comparison_target_model_version": "",
            "comparison_target_feature_version": "",
            "accuracy_diff": None,
            "high_conf_accuracy_diff": None,
            "mae_diff": None,
            "comparison_result": ShihyoPreopenWeeklyReview.COMPARISON_PENDING,

            "review_payload": review_payload,
        }

        obj, created = ShihyoPreopenWeeklyReview.objects.update_or_create(
            week_start_date=week_start_date,
            week_end_date=week_end_date,
            slot=ShihyoPreopenWeeklyReview.SLOT_PREOPEN_0700,
            model_version=model_version,
            feature_version=feature_version,
            defaults=defaults,
        )

        action = "created" if created else "updated"

        self.stdout.write(
            self.style.SUCCESS(
                f"[shihyo_build_preopen_weekly_review] {action} "
                f"week={obj.week_start_date}..{obj.week_end_date} "
                f"sample_count={obj.sample_count} "
                f"direction_accuracy={obj.direction_accuracy} "
                f"high_conf_direction_accuracy={obj.high_conf_direction_accuracy} "
                f"weakness={obj.weakness_code or '-'} "
                f"proposal={obj.proposal_code or '-'} "
                f"status={obj.status}"
            )
        )