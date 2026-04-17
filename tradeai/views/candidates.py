# =========================================================
# [FILE] candidates.py
# [PATH] <project_root>/tradeai/views/candidates.py
#
# このファイルは何？
# - tradeai の候補抽出ページです。
# - ユニバース全体から、ロング候補 / ショート候補を本実装で表示します。
# =========================================================

from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from tradeai.services.candidates.candidate_service import build_candidate_rows


@login_required
def candidates_page(request):
    context = build_candidate_rows(request.user, limit_per_side=8)
    context["page_title"] = "候補抽出"
    return render(request, "tradeai/candidates.html", context)