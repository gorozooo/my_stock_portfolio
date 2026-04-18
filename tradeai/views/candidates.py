# =========================================================
# [FILE] candidates.py
# [PATH] <project_root>/tradeai/views/candidates.py
#
# このファイルは何？
# - tradeai の候補抽出ページです。
# - 今回は「その場で重い候補抽出を実行する」のをやめて、
#   保存済みの CandidateSnapshot を読むだけに変更します。
# - これで 504 Gateway Time-out を避けます。
# =========================================================

from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from tradeai.models.candidate_snapshot import CandidateSnapshot


@login_required
def candidates_page(request):
    snapshot = (
        CandidateSnapshot.objects.filter(user=request.user)
        .order_by("-built_at", "-id")
        .first()
    )

    if snapshot:
        context = {
            "page_title": "候補抽出",
            "snapshot_exists": True,
            "snapshot_built_at": snapshot.built_at,
            "score_max": snapshot.score_max,
            "universe_count": snapshot.universe_count,
            "candidate_total": snapshot.candidate_total,
            "long_candidate_count": snapshot.long_candidate_count,
            "short_candidate_count": snapshot.short_candidate_count,
            "strong_candidate_count": snapshot.strong_candidate_count,
            "attention_candidate_count": snapshot.attention_candidate_count,
            "universe_source_counts": snapshot.universe_source_counts or {},
            "candidate_source_counts": snapshot.candidate_source_counts or {},
            "last_regime": snapshot.last_regime or {},
            "debug_stats": snapshot.debug_stats or {},
            "long_rows": snapshot.long_rows or [],
            "short_rows": snapshot.short_rows or [],
        }
    else:
        context = {
            "page_title": "候補抽出",
            "snapshot_exists": False,
            "snapshot_built_at": None,
            "score_max": 100,
            "universe_count": 0,
            "candidate_total": 0,
            "long_candidate_count": 0,
            "short_candidate_count": 0,
            "strong_candidate_count": 0,
            "attention_candidate_count": 0,
            "universe_source_counts": {
                "holding": 0,
                "watchlist": 0,
                "nikkei225": 0,
                "topix": 0,
                "growth": 0,
            },
            "candidate_source_counts": {
                "holding": 0,
                "watchlist": 0,
                "nikkei225": 0,
                "topix": 0,
                "growth": 0,
            },
            "last_regime": {},
            "debug_stats": {},
            "long_rows": [],
            "short_rows": [],
        }

    return render(request, "tradeai/candidates.html", context)