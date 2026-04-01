# [FILE] holding_attention.py
# [PATH] portfolio/views/holding_attention.py
#
# このファイルは何？
# - /holdings/attention/ の表示専用 view
# - 保有データから「要注意」ページの表示用コンテキストを作る
#
# 今回の方針
# - フィルターなし
# - 証券会社タブ / 対象タブ / 注意カテゴリタブをJSで再読込なし切替
# - 集計自体は attention_service に寄せる
# - サブナビは4ページ共通で、常に相互遷移できるようにする

# -*- coding: utf-8 -*-
from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.urls import reverse

from ..models import Holding
from ..services.holdings import valuation_service as val
from ..services.holdings import attention_service as att


def _build_subnav(active_key: str = "attention"):
    items = [
        {"key": "holdings", "label": "保有", "url": reverse("holding_list")},
        {"key": "summary", "label": "サマリー", "url": reverse("holding_summary")},
        {"key": "attention", "label": "要注意", "url": reverse("holding_attention")},
        {"key": "ai", "label": "AI提案", "url": reverse("holding_ai")},
    ]
    for item in items:
        item["is_active"] = item["key"] == active_key
    return items


@login_required
def holding_attention(request):
    qs = (
        Holding.objects.filter(user=request.user)
        .prefetch_related("dividends")
        .order_by("broker", "account", "-updated_at", "-id")
    )

    rows = val.build_rows_for_queryset(qs)

    active_broker = att.normalize_broker(request.GET.get("broker"))
    active_scope = att.normalize_scope(request.GET.get("scope"))
    active_category = att.normalize_category(request.GET.get("category"))

    ctx = att.build_attention_context(
        rows=rows,
        active_broker=active_broker,
        active_scope=active_scope,
        active_category=active_category,
    )
    ctx["subnav_items"] = _build_subnav("attention")

    return render(request, "holdings/attention.html", ctx)