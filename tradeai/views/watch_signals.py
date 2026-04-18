# =========================================================
# [FILE] watch_signals.py
# [PATH] <project_root>/tradeai/views/watch_signals.py
#
# このファイルは何？
# - tradeai のウォッチリスト監視画面を表示する view です。
# - ロング注目 / ショート注目 / 様子見 を表示します。
#
# 今回の修正：
# - 各行に、日本語名優先 + 33業種セクターを付与する
# =========================================================

from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from tradeai.services.common.sector33_service import enrich_row_dicts_with_profile
from tradeai.services.watchlist.monitor_service import build_watch_signal_rows


@login_required
def watch_signals_page(request):
    rows = build_watch_signal_rows(request.user)
    enrich_row_dicts_with_profile(rows)

    strong_count = sum(1 for row in rows if row["level"] == "STRONG")
    attention_count = sum(1 for row in rows if row["level"] == "ATTENTION")
    reference_count = sum(1 for row in rows if row["level"] == "REFERENCE")

    context = {
        "page_title": "ウォッチ監視",
        "rows": rows,
        "strong_count": strong_count,
        "attention_count": attention_count,
        "reference_count": reference_count,
    }
    return render(request, "tradeai/watch_signals.html", context)