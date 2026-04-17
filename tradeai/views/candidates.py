# =========================================================
# [FILE] candidates.py
# [PATH] <project_root>/tradeai/views/candidates.py
#
# このファイルは何？
# - tradeai の候補抽出ページです。
# - まだ本実装前なので、現状の位置づけと次に入る内容を正直に表示します。
# =========================================================

from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from tradeai.services.watchlist.monitor_service import build_watch_signal_rows


@login_required
def candidates_page(request):
    watch_rows = build_watch_signal_rows(request.user)
    provisional_rows = [row for row in watch_rows if row["level"] in ("STRONG", "ATTENTION")][:5]

    context = {
        "page_title": "候補抽出",
        "provisional_rows": provisional_rows,
    }
    return render(request, "tradeai/candidates.html", context)