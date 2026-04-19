# =========================================================
# [FILE] bias_service.py
# [PATH] <project_root>/tradeai/services/learning/bias_service.py
#
# このファイルは何？
# - LearningResult を集計して、
#   自動エントリー時に使う「学習バイアス」を返すサービスです。
# - 件数が少ない間は無理に効かせず、
#   十分たまってきた時だけ少し優先 / 少し抑制します。
# =========================================================

from __future__ import annotations

from decimal import Decimal
from typing import Any

from tradeai.models.learning_result import LearningResult


DEFAULT_MIN_COUNT = 5
MAX_TOTAL_BIAS = 3


def _to_decimal(value: Any) -> Decimal:
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _safe_rate(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return round((numerator / denominator) * 100.0, 1)


def _safe_avg(total: Decimal, count: int) -> Decimal | None:
    if count <= 0:
        return None
    return total / Decimal(str(count))


def _score_band_label(score_value: int | float | Decimal | None) -> str:
    score = float(score_value or 0)

    if score >= 70:
        return "70点以上"
    if score >= 60:
        return "60〜69点"
    if score >= 50:
        return "50〜59点"
    return "49点以下"


def _empty_bucket(label: str) -> dict[str, Any]:
    return {
        "label": label,
        "count": 0,
        "win_count": 0,
        "loss_count": 0,
        "flat_count": 0,
        "win_rate": None,
        "total_pnl": Decimal("0"),
        "avg_pnl": None,
        "bias": 0,
    }


def _calc_bucket_bias(
    *,
    count: int,
    win_rate: float | None,
    avg_pnl: Decimal | None,
    min_count: int,
) -> int:
    if count < min_count or win_rate is None or avg_pnl is None:
        return 0

    avg_pnl_value = float(avg_pnl)

    # 良い条件を少し優先
    if count >= 10 and win_rate >= 65.0 and avg_pnl_value > 0:
        return 2
    if win_rate >= 55.0 and avg_pnl_value > 0:
        return 1

    # 悪い条件を少し抑制
    if count >= 10 and win_rate <= 35.0 and avg_pnl_value < 0:
        return -2
    if win_rate <= 45.0 and avg_pnl_value < 0:
        return -1

    return 0


def _finalize_bucket(bucket: dict[str, Any], min_count: int) -> dict[str, Any]:
    bucket["win_rate"] = _safe_rate(bucket["win_count"], bucket["count"])
    bucket["avg_pnl"] = _safe_avg(bucket["total_pnl"], bucket["count"])
    bucket["bias"] = _calc_bucket_bias(
        count=bucket["count"],
        win_rate=bucket["win_rate"],
        avg_pnl=bucket["avg_pnl"],
        min_count=min_count,
    )
    return bucket


def build_learning_bias_context(user, min_count: int = DEFAULT_MIN_COUNT) -> dict[str, Any]:
    results = list(
        LearningResult.objects.filter(snapshot__user=user).select_related("snapshot").order_by("-settled_at", "-id")
    )

    direction_buckets = {
        "LONG": _empty_bucket("LONG"),
        "SHORT": _empty_bucket("SHORT"),
    }
    source_buckets = {
        "HOLDING": _empty_bucket("保有由来"),
        "WATCHLIST": _empty_bucket("ウォッチ由来"),
        "UNIVERSE": _empty_bucket("母集団由来"),
    }
    score_band_buckets = {
        "70点以上": _empty_bucket("70点以上"),
        "60〜69点": _empty_bucket("60〜69点"),
        "50〜59点": _empty_bucket("50〜59点"),
        "49点以下": _empty_bucket("49点以下"),
    }

    for result in results:
        snapshot = result.snapshot
        pnl = _to_decimal(result.pnl_yen)

        direction_key = str(snapshot.direction or "").upper().strip()
        if direction_key in direction_buckets:
            bucket = direction_buckets[direction_key]
            bucket["count"] += 1
            bucket["total_pnl"] += pnl
            if result.result_label == LearningResult.ResultLabelChoices.WIN:
                bucket["win_count"] += 1
            elif result.result_label == LearningResult.ResultLabelChoices.LOSS:
                bucket["loss_count"] += 1
            elif result.result_label == LearningResult.ResultLabelChoices.FLAT:
                bucket["flat_count"] += 1

        source_key = str(snapshot.source_scope or "").upper().strip()
        if source_key in source_buckets:
            bucket = source_buckets[source_key]
            bucket["count"] += 1
            bucket["total_pnl"] += pnl
            if result.result_label == LearningResult.ResultLabelChoices.WIN:
                bucket["win_count"] += 1
            elif result.result_label == LearningResult.ResultLabelChoices.LOSS:
                bucket["loss_count"] += 1
            elif result.result_label == LearningResult.ResultLabelChoices.FLAT:
                bucket["flat_count"] += 1

        band_label = _score_band_label(snapshot.signal_score)
        bucket = score_band_buckets[band_label]
        bucket["count"] += 1
        bucket["total_pnl"] += pnl
        if result.result_label == LearningResult.ResultLabelChoices.WIN:
            bucket["win_count"] += 1
        elif result.result_label == LearningResult.ResultLabelChoices.LOSS:
            bucket["loss_count"] += 1
        elif result.result_label == LearningResult.ResultLabelChoices.FLAT:
            bucket["flat_count"] += 1

    for key, bucket in direction_buckets.items():
        direction_buckets[key] = _finalize_bucket(bucket, min_count)

    for key, bucket in source_buckets.items():
        source_buckets[key] = _finalize_bucket(bucket, min_count)

    for key, bucket in score_band_buckets.items():
        score_band_buckets[key] = _finalize_bucket(bucket, min_count)

    return {
        "min_count": min_count,
        "total_result_count": len(results),
        "direction_buckets": direction_buckets,
        "source_buckets": source_buckets,
        "score_band_buckets": score_band_buckets,
    }


def _build_bucket_note(bucket: dict[str, Any]) -> str:
    bias = int(bucket.get("bias") or 0)
    count = int(bucket.get("count") or 0)
    win_rate = bucket.get("win_rate")

    if bias == 0 or count <= 0 or win_rate is None:
        return ""

    sign = "+" if bias > 0 else ""
    return f"{bucket['label']} {sign}{bias}（勝率{win_rate:.1f}% / {count}件）"


def evaluate_candidate_learning_bias(
    learning_context: dict[str, Any],
    *,
    direction: str,
    source_scope: str,
    score_100: int | float | Decimal | None,
) -> dict[str, Any]:
    direction_key = str(direction or "").upper().strip()
    source_key = str(source_scope or "").upper().strip()
    score_band_label = _score_band_label(score_100)

    direction_bucket = learning_context.get("direction_buckets", {}).get(direction_key, _empty_bucket(direction_key))
    source_bucket = learning_context.get("source_buckets", {}).get(source_key, _empty_bucket(source_key))
    score_bucket = learning_context.get("score_band_buckets", {}).get(score_band_label, _empty_bucket(score_band_label))

    total_bias = int(direction_bucket.get("bias") or 0)
    total_bias += int(source_bucket.get("bias") or 0)
    total_bias += int(score_bucket.get("bias") or 0)

    if total_bias > MAX_TOTAL_BIAS:
        total_bias = MAX_TOTAL_BIAS
    if total_bias < -MAX_TOTAL_BIAS:
        total_bias = -MAX_TOTAL_BIAS

    notes: list[str] = []

    direction_note = _build_bucket_note(direction_bucket)
    if direction_note:
        notes.append(direction_note)

    source_note = _build_bucket_note(source_bucket)
    if source_note:
        notes.append(source_note)

    score_note = _build_bucket_note(score_bucket)
    if score_note:
        notes.append(score_note)

    return {
        "total_bias": total_bias,
        "score_band_label": score_band_label,
        "notes": notes,
    }