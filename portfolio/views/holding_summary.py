# [FILE] holding_summary.py
# [PATH] portfolio/views/holding_summary.py
#
# このファイルは何？
# - /holdings/summary/ の専用 view
# - 証券会社タブ / 対象タブ / 分析ブロックをまとめて描画する
#
# 今回の方針
# - 保有ページとは完全に分離した分析専用ページ
# - 初回描画時に必要な組み合わせを全部作っておき、
#   タブ切替はフロント側で行う
# - 対象タブは「全体 / 現物 / 信用」
# - サブナビは4ページ共通で、常に相互遷移できるようにする

# -*- coding: utf-8 -*-
from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.urls import reverse

from ..models import Holding
from ..services.holdings import valuation_service as val
from ..services.holdings import summary_service as sumsvc


BROKER_TABS = [
    ("ALL", "全体"),
    ("RAKUTEN", "楽天"),
    ("MATSUI", "松井"),
    ("SBI", "SBI"),
]

SCOPE_TABS = [
    ("all", "全体"),
    ("spot", "現物"),
    ("margin", "信用"),
]

BLOCK_TABS = [
    ("state", "全体状態"),
    ("allocation", "配分"),
    ("profit", "損益"),
    ("time", "時間"),
    ("dividend", "配当"),
    ("risk", "リスク"),
    ("comment", "コメント"),
]


def _normalize_broker(raw: str | None) -> str:
    value = (raw or "").strip().upper()
    valid = {x[0] for x in BROKER_TABS}
    if value in valid:
        return value
    return "ALL"


def _normalize_scope(raw: str | None) -> str:
    value = (raw or "").strip().lower()
    valid = {x[0] for x in SCOPE_TABS}
    if value in valid:
        return value
    return "all"


def _build_subnav(active_key: str = "summary"):
    items = [
        {"key": "holdings", "label": "保有", "url": reverse("holding_list")},
        {"key": "summary", "label": "サマリー", "url": reverse("holding_summary")},
        {"key": "attention", "label": "要注意", "url": reverse("holding_attention")},
        {"key": "ai", "label": "AI提案", "url": reverse("holding_ai")},
    ]
    for item in items:
        item["is_active"] = item["key"] == active_key
    return items


def _filter_rows(rows, broker_key: str, scope_key: str):
    out = []
    for r in rows:
        h = r.obj
        broker = (getattr(h, "broker", "") or "").upper()
        account = (getattr(h, "account", "") or "").upper()

        if broker_key != "ALL" and broker != broker_key:
            continue

        if scope_key == "spot" and account not in ("SPEC", "NISA"):
            continue
        if scope_key == "margin" and account != "MARGIN":
            continue

        out.append(r)
    return out


@login_required
def holding_summary(request):
    active_broker = _normalize_broker(request.GET.get("broker"))
    active_scope = _normalize_scope(request.GET.get("scope"))

    qs = (
        Holding.objects.filter(user=request.user)
        .prefetch_related("dividends")
        .order_by("broker", "account", "-updated_at", "-id")
    )
    rows = val.build_rows_for_queryset(qs)

    broker_tabs = []
    combo_panels = []

    broker_scope_counts: dict[str, dict[str, int]] = {}

    for broker_key, broker_label in BROKER_TABS:
        broker_scope_counts[broker_key] = {}
        for scope_key, _scope_label in SCOPE_TABS:
            filtered = _filter_rows(rows, broker_key, scope_key)
            broker_scope_counts[broker_key][scope_key] = len(filtered)
            combo_panels.append(
                {
                    "broker_key": broker_key,
                    "scope_key": scope_key,
                    "summary": sumsvc.build_summary(filtered),
                    "count": len(filtered),
                }
            )

    for broker_key, broker_label in BROKER_TABS:
        counts = broker_scope_counts[broker_key]
        broker_tabs.append(
            {
                "key": broker_key,
                "label": broker_label,
                "count": counts["all"],
                "counts": counts,
                "is_active": broker_key == active_broker,
            }
        )

    scope_tabs = []
    active_counts = broker_scope_counts.get(
        active_broker,
        {"all": 0, "spot": 0, "margin": 0},
    )
    for scope_key, scope_label in SCOPE_TABS:
        scope_tabs.append(
            {
                "key": scope_key,
                "label": scope_label,
                "count": active_counts.get(scope_key, 0),
                "is_active": scope_key == active_scope,
            }
        )

    block_tabs = []
    for block_key, block_label in BLOCK_TABS:
        block_tabs.append(
            {
                "key": block_key,
                "label": block_label,
                "is_active": block_key == "state",
            }
        )

    ctx = {
        "subnav_items": _build_subnav("summary"),
        "broker_tabs": broker_tabs,
        "scope_tabs": scope_tabs,
        "block_tabs": block_tabs,
        "combo_panels": combo_panels,
        "active_broker": active_broker,
        "active_scope": active_scope,
        "active_block": "state",
    }
    return render(request, "holdings/summary.html", ctx)