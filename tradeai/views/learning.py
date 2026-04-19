# =========================================================
# [FILE] learning.py
# [PATH] <project_root>/tradeai/views/learning.py
#
# このファイルは何？
# - tradeai の学習確認ページです。
# - LearningSnapshot / LearningResult を集計して、
#   学習材料がどれだけ溜まっているか、
#   どの方向・由来・点数帯が良いかを表示します。
# =========================================================

from __future__ import annotations

from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from tradeai.models.learning_result import LearningResult
from tradeai.models.learning_snapshot import LearningSnapshot


SOURCE_LABELS = {
    "HOLDING": "保有由来",
    "WATCHLIST": "ウォッチ由来",
    "UNIVERSE": "母集団由来",
}

DIRECTION_LABELS = {
    "LONG": "LONG",
    "SHORT": "SHORT",
}


def _to_decimal(value) -> Decimal:
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _safe_rate(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return round((numerator / denominator) * 100.0, 1)


def _safe_avg_decimal(total: Decimal, count: int) -> Decimal | None:
    if count <= 0:
        return None
    return total / Decimal(str(count))


def _empty_bucket(label: str) -> dict:
    return {
        "label": label,
        "count": 0,
        "win_count": 0,
        "loss_count": 0,
        "flat_count": 0,
        "win_rate": None,
        "total_pnl": Decimal("0"),
        "avg_pnl": None,
    }


def _finalize_bucket(bucket: dict) -> dict:
    bucket["win_rate"] = _safe_rate(bucket["win_count"], bucket["count"])
    bucket["avg_pnl"] = _safe_avg_decimal(bucket["total_pnl"], bucket["count"])
    return bucket


def _build_direction_summaries(results: list[LearningResult]) -> list[dict]:
    buckets = {
        "LONG": _empty_bucket("LONG"),
        "SHORT": _empty_bucket("SHORT"),
    }

    for result in results:
        key = str(result.snapshot.direction or "").upper().strip()
        if key not in buckets:
            continue

        bucket = buckets[key]
        bucket["count"] += 1
        bucket["total_pnl"] += _to_decimal(result.pnl_yen)

        if result.result_label == LearningResult.ResultLabelChoices.WIN:
            bucket["win_count"] += 1
        elif result.result_label == LearningResult.ResultLabelChoices.LOSS:
            bucket["loss_count"] += 1
        elif result.result_label == LearningResult.ResultLabelChoices.FLAT:
            bucket["flat_count"] += 1

    return [
        _finalize_bucket(buckets["LONG"]),
        _finalize_bucket(buckets["SHORT"]),
    ]


def _build_source_summaries(results: list[LearningResult]) -> list[dict]:
    buckets = {
        "HOLDING": _empty_bucket("保有由来"),
        "WATCHLIST": _empty_bucket("ウォッチ由来"),
        "UNIVERSE": _empty_bucket("母集団由来"),
    }

    for result in results:
        key = str(result.snapshot.source_scope or "").upper().strip()
        if key not in buckets:
            continue

        bucket = buckets[key]
        bucket["count"] += 1
        bucket["total_pnl"] += _to_decimal(result.pnl_yen)

        if result.result_label == LearningResult.ResultLabelChoices.WIN:
            bucket["win_count"] += 1
        elif result.result_label == LearningResult.ResultLabelChoices.LOSS:
            bucket["loss_count"] += 1
        elif result.result_label == LearningResult.ResultLabelChoices.FLAT:
            bucket["flat_count"] += 1

    return [
        _finalize_bucket(buckets["HOLDING"]),
        _finalize_bucket(buckets["WATCHLIST"]),
        _finalize_bucket(buckets["UNIVERSE"]),
    ]


def _score_band_label(score_value: Decimal) -> str:
    score = float(score_value or 0)

    if score >= 70:
        return "70点以上"
    if score >= 60:
        return "60〜69点"
    if score >= 50:
        return "50〜59点"
    return "49点以下"


def _build_score_band_summaries(results: list[LearningResult]) -> list[dict]:
    ordered_labels = ["70点以上", "60〜69点", "50〜59点", "49点以下"]
    buckets = {label: _empty_bucket(label) for label in ordered_labels}

    for result in results:
        score_value = _to_decimal(result.snapshot.signal_score)
        label = _score_band_label(score_value)
        bucket = buckets[label]

        bucket["count"] += 1
        bucket["total_pnl"] += _to_decimal(result.pnl_yen)

        if result.result_label == LearningResult.ResultLabelChoices.WIN:
            bucket["win_count"] += 1
        elif result.result_label == LearningResult.ResultLabelChoices.LOSS:
            bucket["loss_count"] += 1
        elif result.result_label == LearningResult.ResultLabelChoices.FLAT:
            bucket["flat_count"] += 1

    return [_finalize_bucket(buckets[label]) for label in ordered_labels]


@login_required
def learning_page(request):
    user = request.user

    snapshot_qs = LearningSnapshot.objects.filter(user=user).select_related("demo_trade")
    result_qs = LearningResult.objects.filter(snapshot__user=user).select_related(
        "snapshot",
        "snapshot__demo_trade",
    )

    snapshot_count = snapshot_qs.count()
    result_count = result_qs.count()
    pending_snapshot_count = snapshot_qs.filter(result__isnull=True).count()

    all_results = list(result_qs.order_by("-settled_at", "-id"))

    win_count = sum(1 for row in all_results if row.result_label == LearningResult.ResultLabelChoices.WIN)
    loss_count = sum(1 for row in all_results if row.result_label == LearningResult.ResultLabelChoices.LOSS)
    flat_count = sum(1 for row in all_results if row.result_label == LearningResult.ResultLabelChoices.FLAT)

    total_pnl = sum((_to_decimal(row.pnl_yen) for row in all_results), Decimal("0"))
    total_hold_days = sum((row.hold_days or 0 for row in all_results), 0)

    win_rate = _safe_rate(win_count, result_count)
    avg_pnl = _safe_avg_decimal(total_pnl, result_count)
    avg_hold_days = round(total_hold_days / result_count, 1) if result_count else None

    direction_summaries = _build_direction_summaries(all_results)
    source_summaries = _build_source_summaries(all_results)
    score_band_summaries = _build_score_band_summaries(all_results)

    recent_results = all_results[:20]
    pending_snapshots = list(
        snapshot_qs.filter(result__isnull=True).order_by("-snapshot_at", "-id")[:20]
    )

    context = {
        "page_title": "学習",
        "snapshot_count": snapshot_count,
        "result_count": result_count,
        "pending_snapshot_count": pending_snapshot_count,
        "win_count": win_count,
        "loss_count": loss_count,
        "flat_count": flat_count,
        "win_rate": win_rate,
        "total_pnl": total_pnl,
        "avg_pnl": avg_pnl,
        "avg_hold_days": avg_hold_days,
        "direction_summaries": direction_summaries,
        "source_summaries": source_summaries,
        "score_band_summaries": score_band_summaries,
        "recent_results": recent_results,
        "pending_snapshots": pending_snapshots,
    }
    return render(request, "tradeai/learning.html", context)