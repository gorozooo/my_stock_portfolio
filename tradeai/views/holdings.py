# =========================================================
# [FILE] holdings.py
# [PATH] <project_root>/tradeai/views/holdings.py
#
# このファイルは何？
# - tradeai の保有銘柄監視画面を表示する view です。
# - ロング/ショート別に、継続・利確・損切りなどの判断を表示します。
# =========================================================

from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from tradeai.services.holdings.monitor_service import build_holding_monitor_rows


@login_required
def holdings_page(request):
    rows = build_holding_monitor_rows(request.user)

    strong_count = sum(1 for row in rows if row["level"] == "STRONG")
    attention_count = sum(1 for row in rows if row["level"] == "ATTENTION")
    reference_count = sum(1 for row in rows if row["level"] == "REFERENCE")

    context = {
        "page_title": "保有監視",
        "rows": rows,
        "strong_count": strong_count,
        "attention_count": attention_count,
        "reference_count": reference_count,
    }
    return render(request, "tradeai/holdings.html", context)