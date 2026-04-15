# =========================================================
# [FILE] api.py
# [PATH] <project_root>/tradeai/views/api.py
#
# このファイルは何？
# - tradeai 用のAPI viewをまとめるファイルです。
# - 今回は証券コードから銘柄名を返すAPIを持ちます。
# =========================================================

from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_GET

from tradeai.services.common.stock_lookup import lookup_stock_name_and_sector


@login_required
@require_GET
def api_ticker_name(request):
    raw = (request.GET.get("code") or request.GET.get("q") or "").strip()
    result = lookup_stock_name_and_sector(raw)
    return JsonResponse(result)