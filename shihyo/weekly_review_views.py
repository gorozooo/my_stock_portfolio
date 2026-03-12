"""
[FILE] weekly_review_views.py
[PATH] <project_root>/shihyo/weekly_review_views.py

このファイルは何？
- 指標アプリ専用の週次レビュー画面 View です。
- /shihyo/weekly-review/ で最新の週次レビュー1件を表示します。
- 同じ画面から 承認 / 保留 / 却下 を更新します。
- 既存の dashboard 用 views.py とは分離した専用ビューです。

今回の修正ポイント：
- 一致率などの % 表示を補正する
  （1.0 を 1.0% ではなく 100.0% として扱う）
- sample_count が少ない間は「データ不足モード」として扱う
- データ不足時は、空の詳細セクションをテンプレート側で隠せるようにする
"""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.utils import timezone

from shihyo.models import ShihyoPreopenWeeklyReview


MIN_WEEKLY_REVIEW_SAMPLE_COUNT = 20


def _system_status_label(status: str) -> str:
    mapping = {
        ShihyoPreopenWeeklyReview.STATUS_STABLE: "安定",
        ShihyoPreopenWeeklyReview.STATUS_PROPOSAL_READY: "候補あり",
        ShihyoPreopenWeeklyReview.STATUS_COOLDOWN: "クールダウン",
        ShihyoPreopenWeeklyReview.STATUS_INSUFFICIENT_DATA: "データ不足",
        ShihyoPreopenWeeklyReview.STATUS_PROPOSAL_APPLIED: "比較中",
        ShihyoPreopenWeeklyReview.STATUS_PROPOSAL_ADOPTED: "採用",
        ShihyoPreopenWeeklyReview.STATUS_PROPOSAL_REJECTED: "不採用",
    }
    return mapping.get(status, status or "-")


def _system_status_class(status: str) -> str:
    if status in {
        ShihyoPreopenWeeklyReview.STATUS_STABLE,
        ShihyoPreopenWeeklyReview.STATUS_PROPOSAL_ADOPTED,
    }:
        return "weekly-badge weekly-badge-green"
    if status in {
        ShihyoPreopenWeeklyReview.STATUS_PROPOSAL_READY,
        ShihyoPreopenWeeklyReview.STATUS_PROPOSAL_APPLIED,
        ShihyoPreopenWeeklyReview.STATUS_COOLDOWN,
    }:
        return "weekly-badge weekly-badge-yellow"
    if status in {
        ShihyoPreopenWeeklyReview.STATUS_PROPOSAL_REJECTED,
        ShihyoPreopenWeeklyReview.STATUS_INSUFFICIENT_DATA,
    }:
        return "weekly-badge weekly-badge-red"
    return "weekly-badge weekly-badge-gray"


def _approval_status_label(status: str) -> str:
    mapping = {
        ShihyoPreopenWeeklyReview.APPROVAL_PENDING: "未確認",
        ShihyoPreopenWeeklyReview.APPROVAL_APPROVED: "承認",
        ShihyoPreopenWeeklyReview.APPROVAL_HOLD: "保留",
        ShihyoPreopenWeeklyReview.APPROVAL_REJECTED: "却下",
    }
    return mapping.get(status, status or "-")


def _approval_status_class(status: str) -> str:
    if status == ShihyoPreopenWeeklyReview.APPROVAL_APPROVED:
        return "weekly-badge weekly-badge-green"
    if status == ShihyoPreopenWeeklyReview.APPROVAL_HOLD:
        return "weekly-badge weekly-badge-yellow"
    if status == ShihyoPreopenWeeklyReview.APPROVAL_REJECTED:
        return "weekly-badge weekly-badge-red"
    return "weekly-badge weekly-badge-gray"


def _format_percent(value) -> str:
    """
    DB内の値が
    - 1.0   -> 100%
    - 0.57  -> 57%
    - 57.0  -> 57%
    のどれで来ても、表示をできるだけ自然にそろえる。
    """
    if value is None:
        return "-"

    try:
        x = float(value)
    except (TypeError, ValueError):
        return "-"

    display_value = x * 100.0 if abs(x) <= 1.0 else x
    return f"{display_value:.1f}%"


def _format_point(value, digits: int = 2) -> str:
    if value is None:
        return "-"
    try:
        x = float(value)
    except (TypeError, ValueError):
        return "-"
    return f"{x:.{digits}f}pt"


def _format_signed_point(value, digits: int = 1) -> str:
    if value is None:
        return "-"
    try:
        x = float(value)
    except (TypeError, ValueError):
        return "-"

    # accuracy_diff などが 0.10 = 10pt の形で入っている可能性を吸収
    display_value = x * 100.0 if abs(x) <= 1.0 else x

    if display_value > 0:
        return f"+{display_value:.{digits}f}pt"
    return f"{display_value:.{digits}f}pt"


def _is_insufficient_review(review: ShihyoPreopenWeeklyReview) -> bool:
    if review.status == ShihyoPreopenWeeklyReview.STATUS_INSUFFICIENT_DATA:
        return True
    return int(review.sample_count or 0) < MIN_WEEKLY_REVIEW_SAMPLE_COUNT


@login_required
def weekly_review(request):
    review = (
        ShihyoPreopenWeeklyReview.objects
        .select_related("approval_updated_by")
        .order_by("-week_start_date", "-created_at")
        .first()
    )

    if request.method == "POST":
        if not review:
            messages.error(request, "週次レビューがまだありません。")
            return redirect("shihyo:weekly_review")

        action = str(request.POST.get("action") or "").strip()
        comment = str(request.POST.get("approval_comment") or "").strip()

        action_map = {
            "approve": ShihyoPreopenWeeklyReview.APPROVAL_APPROVED,
            "hold": ShihyoPreopenWeeklyReview.APPROVAL_HOLD,
            "reject": ShihyoPreopenWeeklyReview.APPROVAL_REJECTED,
        }

        if action not in action_map:
            messages.error(request, "承認操作が不正です。")
            return redirect("shihyo:weekly_review")

        review.approval_status = action_map[action]
        review.approval_comment = comment
        review.approval_updated_at = timezone.now()
        review.approval_updated_by = request.user
        review.save(
            update_fields=[
                "approval_status",
                "approval_comment",
                "approval_updated_at",
                "approval_updated_by",
                "updated_at",
            ]
        )

        if action == "approve":
            messages.success(request, "週次レビューを承認しました。")
        elif action == "hold":
            messages.success(request, "週次レビューを保留にしました。")
        else:
            messages.success(request, "週次レビューを却下にしました。")

        return redirect("shihyo:weekly_review")

    review_is_insufficient = _is_insufficient_review(review) if review else True

    context = {
        "review": review,
        "system_status_label": _system_status_label(review.status) if review else "-",
        "system_status_class": _system_status_class(review.status) if review else "weekly-badge weekly-badge-gray",
        "approval_status_label": _approval_status_label(review.approval_status) if review else "未確認",
        "approval_status_class": _approval_status_class(review.approval_status) if review else "weekly-badge weekly-badge-gray",

        "review_is_insufficient": review_is_insufficient,
        "show_detailed_sections": bool(review and not review_is_insufficient),
        "min_weekly_review_sample_count": MIN_WEEKLY_REVIEW_SAMPLE_COUNT,

        "direction_accuracy_display": _format_percent(review.direction_accuracy) if review else "-",
        "high_conf_direction_accuracy_display": _format_percent(review.high_conf_direction_accuracy) if review else "-",
        "mean_abs_error_pct_display": _format_point(review.mean_abs_error_pct, 2) if review else "-",

        "weakness_accuracy_display": _format_percent(review.weakness_accuracy) if review else "-",
        "weakness_gap_vs_overall_display": _format_signed_point(review.weakness_gap_vs_overall, 1) if review else "-",

        "accuracy_diff_display": _format_signed_point(review.accuracy_diff, 1) if review else "-",
        "high_conf_accuracy_diff_display": _format_signed_point(review.high_conf_accuracy_diff, 1) if review else "-",
        "mae_diff_display": _format_signed_point(review.mae_diff, 2) if review else "-",
    }
    return render(request, "shihyo/weekly_review.html", context)