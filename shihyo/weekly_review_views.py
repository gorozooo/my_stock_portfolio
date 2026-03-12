"""
[FILE] weekly_review_views.py
[PATH] <project_root>/shihyo/weekly_review_views.py

このファイルは何？
- 指標アプリ専用の週次レビュー画面 View です。
- /shihyo/weekly-review/ で最新の週次レビュー1件を表示します。
- 同じ画面から 承認 / 保留 / 却下 を更新します。
- 既存の dashboard 用 views.py とは分離した専用ビューです。
"""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.utils import timezone

from shihyo.models import ShihyoPreopenWeeklyReview


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

    context = {
        "review": review,
        "system_status_label": _system_status_label(review.status) if review else "-",
        "system_status_class": _system_status_class(review.status) if review else "weekly-badge weekly-badge-gray",
        "approval_status_label": _approval_status_label(review.approval_status) if review else "未確認",
        "approval_status_class": _approval_status_class(review.approval_status) if review else "weekly-badge weekly-badge-gray",
    }
    return render(request, "shihyo/weekly_review.html", context)