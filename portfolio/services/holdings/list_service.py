# [FILE] list_service.py
# [PATH] portfolio/services/holdings/list_service.py
#
# このファイルは何？
# - 保有一覧ページ（/holdings/）専用の表示データ生成サービス
# - brokerタブ / bucketタブ / 一覧行 / 口座区分ごとの並びをまとめて作る
#
# 今回の方針
# - 保有ページは「見る・触る」専用
# - KPI / フィルター / ソート / スパークは持ち込まない
# - 表示用の重い計算は valuation_service を利用し、このサービスで画面向けに整形する

# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Optional
from django.db.models import Count
from django.urls import reverse

from ...models import Holding
from . import valuation_service as val


BROKER_ORDER = [
    ("RAKUTEN", "楽天"),
    ("MATSUI", "松井"),
    ("SBI", "SBI"),
]

BUCKET_ORDER = [
    ("all", "全体"),
    ("spec", "特定"),
    ("nisa", "NISA"),
    ("margin", "信用"),
]

BUCKET_TO_ACCOUNT = {
    "spec": "SPEC",
    "nisa": "NISA",
    "margin": "MARGIN",
}

ACCOUNT_SECTION_ORDER = [
    ("SPEC", "特定"),
    ("NISA", "NISA"),
    ("MARGIN", "信用"),
]


def _normalize_broker(raw: Optional[str], user) -> str:
    value = (raw or "").strip().upper()
    valid = {x[0] for x in BROKER_ORDER}
    if value in valid:
        return value

    user_brokers = list(
        Holding.objects.filter(user=user, broker__in=list(valid))
        .values("broker")
        .annotate(c=Count("id"))
        .order_by()
    )
    broker_codes = [x["broker"] for x in user_brokers]

    for code, _label in BROKER_ORDER:
        if code in broker_codes:
            return code

    return "RAKUTEN"


def _normalize_bucket(raw: Optional[str]) -> str:
    value = (raw or "").strip().lower()
    valid = {x[0] for x in BUCKET_ORDER}
    if value in valid:
        return value
    return "all"


def _build_holdings_url(broker: str, bucket: str) -> str:
    return f"{reverse('holding_list')}?broker={broker}&bucket={bucket}"


def _build_subnav():
    return [
        {"key": "holdings", "label": "保有", "url": reverse("holding_list"), "is_active": True},
        {"key": "summary", "label": "サマリー", "url": None, "is_active": False},
        {"key": "attention", "label": "要注意", "url": None, "is_active": False},
        {"key": "ai", "label": "AI提案", "url": None, "is_active": False},
    ]


def _broker_tabs(user, active_broker: str, active_bucket: str):
    counts_qs = (
        Holding.objects.filter(user=user, broker__in=[x[0] for x in BROKER_ORDER])
        .values("broker")
        .annotate(c=Count("id"))
        .order_by()
    )
    count_map = {row["broker"]: int(row["c"] or 0) for row in counts_qs}

    tabs = []
    for code, label in BROKER_ORDER:
        tabs.append(
            {
                "key": code,
                "label": label,
                "count": count_map.get(code, 0),
                "is_active": code == active_broker,
                "url": _build_holdings_url(code, active_bucket),
            }
        )
    return tabs


def _bucket_tabs(user, active_broker: str, active_bucket: str):
    broker_qs = Holding.objects.filter(user=user, broker=active_broker)

    account_counts_qs = (
        broker_qs.values("account")
        .annotate(c=Count("id"))
        .order_by()
    )
    account_count_map = {row["account"]: int(row["c"] or 0) for row in account_counts_qs}

    all_count = int(broker_qs.count())

    tabs = []
    for key, label in BUCKET_ORDER:
        if key == "all":
            count = all_count
        else:
            count = account_count_map.get(BUCKET_TO_ACCOUNT[key], 0)

        tabs.append(
            {
                "key": key,
                "label": label,
                "count": count,
                "is_active": key == active_bucket,
                "url": _build_holdings_url(active_broker, key),
            }
        )
    return tabs


def _base_queryset(user, active_broker: str):
    return (
        Holding.objects.filter(user=user, broker=active_broker)
        .prefetch_related("dividends")
        .order_by("-updated_at", "-id")
    )


def _filter_rows_by_bucket(rows, active_bucket: str):
    if active_bucket == "all":
        return rows

    target_account = BUCKET_TO_ACCOUNT.get(active_bucket)
    if not target_account:
        return rows

    return [r for r in rows if getattr(r.obj, "account", "") == target_account]


def _build_sections(rows, active_bucket: str):
    sections = []

    if active_bucket == "all":
        for account_code, account_label in ACCOUNT_SECTION_ORDER:
            section_rows = [r for r in rows if getattr(r.obj, "account", "") == account_code]
            if section_rows:
                sections.append(
                    {
                        "key": account_code,
                        "label": account_label,
                        "count": len(section_rows),
                        "rows": section_rows,
                    }
                )
        return sections

    target_account = BUCKET_TO_ACCOUNT.get(active_bucket)
    target_label = dict(BUCKET_ORDER).get(active_bucket, "全体")
    section_rows = [r for r in rows if getattr(r.obj, "account", "") == target_account]
    if section_rows:
        sections.append(
            {
                "key": target_account,
                "label": target_label,
                "count": len(section_rows),
                "rows": section_rows,
            }
        )
    return sections


def _attach_row_flags(rows):
    for r in rows:
        h = r.obj
        r.can_margin_to_spot = (getattr(h, "account", "") == "MARGIN" and getattr(h, "side", "") == "BUY")
    return rows


def build_holdings_list_context(user, broker: Optional[str], bucket: Optional[str]) -> dict:
    active_broker = _normalize_broker(broker, user)
    active_bucket = _normalize_bucket(bucket)

    qs = _base_queryset(user, active_broker)
    rows = val.build_rows_for_queryset(qs)
    rows = _attach_row_flags(rows)
    rows = _filter_rows_by_bucket(rows, active_bucket)
    sections = _build_sections(rows, active_bucket)

    broker_label_map = dict(BROKER_ORDER)
    bucket_label_map = dict(BUCKET_ORDER)

    empty_message = f"{broker_label_map.get(active_broker, active_broker)} / {bucket_label_map.get(active_bucket, active_bucket)} の保有はまだありません。"

    return {
        "subnav_items": _build_subnav(),
        "broker_tabs": _broker_tabs(user, active_broker, active_bucket),
        "bucket_tabs": _bucket_tabs(user, active_broker, active_bucket),
        "active_broker": active_broker,
        "active_bucket": active_bucket,
        "sections": sections,
        "has_rows": bool(rows),
        "empty_message": empty_message,
    }