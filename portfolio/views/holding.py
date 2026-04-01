# [FILE] holding.py
# [PATH] portfolio/views/holding.py
#
# このファイルは何？
# - Holding 一覧表示と銘柄APIだけを担当する view
#
# 今回の目的
# - 保有ページのサブナビから summary に遷移できるようにする
# - 保有ページ自体は「操作専用」を維持する
#
# 今回の方針
# - /holdings/ は 1ページ完結
# - holding_list_partial は互換のため残す
# - 評価・損益などの計算は valuation_service を使う

# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Dict, Optional, Tuple
import time

import yfinance as yf
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render
from django.urls import reverse

from ..models import Holding
from ..services import trend as svc_trend
from ..services.holdings import valuation_service as val


SECTOR_CACHE_TTL = 30 * 60
_SECTOR_CACHE: Dict[str, Tuple[float, str]] = {}

BROKER_ORDER = [
    ("RAKUTEN", "楽天"),
    ("MATSUI", "松井"),
    ("SBI", "SBI"),
]

ACCOUNT_ORDER = [
    ("SPEC", "特定"),
    ("NISA", "NISA"),
    ("MARGIN", "信用"),
]

BUCKET_ORDER = [
    ("all", "全体"),
    ("spec", "特定"),
    ("nisa", "NISA"),
    ("margin", "信用"),
]


def _sector_cache_get(norm: str) -> Optional[str]:
    item = _SECTOR_CACHE.get(norm)
    if not item:
        return None
    ts, sec = item
    if time.time() - ts < SECTOR_CACHE_TTL:
        return sec
    return None


def _sector_cache_put(norm: str, sector: str) -> None:
    if sector:
        _SECTOR_CACHE[norm] = (time.time(), sector)


@login_required
def api_ticker_name(request):
    raw = (request.GET.get("code") or request.GET.get("q") or "").strip()
    norm = svc_trend._normalize_ticker(raw)
    code = (norm.split(".", 1)[0] if norm else raw).upper()

    name = ""
    sector_hint = None

    override = getattr(settings, "TSE_NAME_OVERRIDES", {}).get(code)
    if override:
        name = override

    if code.isdigit():
        m_name, m_sector = svc_trend.lookup_master_name_and_sector(norm)
        if m_name and not name:
            name = m_name
        if m_sector:
            sector_hint = m_sector

    if not name:
        name = svc_trend._lookup_name_jp_from_list(norm) or ""
        if not name:
            try:
                name = svc_trend._fetch_name_prefer_jp(norm) or ""
            except Exception:
                name = ""

    cached = _sector_cache_get(norm)
    if cached:
        sector = cached
    else:
        sector = None

        if sector_hint:
            sector = sector_hint

        if not sector:
            try:
                sector = svc_trend._fetch_sector_prefer_jp(norm) or None
            except Exception:
                sector = None

        if not sector:
            try:
                info = yf.Ticker(norm).get_info()
                sec_en = (info or {}).get("sector") or (info or {}).get("industry") or ""
                map_en2jp = {
                    "Technology": "情報・通信業",
                    "Communication Services": "情報・通信業",
                    "Industrials": "機械",
                    "Consumer Cyclical": "小売業",
                    "Consumer Defensive": "食料品",
                    "Financial Services": "銀行業",
                    "Real Estate": "不動産業",
                    "Healthcare": "医薬品",
                    "Basic Materials": "化学",
                    "Energy": "石油・石炭製品",
                    "Utilities": "電気・ガス業",
                }
                sector = map_en2jp.get(str(sec_en), str(sec_en)) or None
            except Exception:
                sector = None

        if sector:
            _sector_cache_put(norm, sector)

    return JsonResponse({"code": code, "name": name, "sector": sector or ""})


def _normalize_broker(raw: Optional[str], user) -> str:
    value = (raw or "").strip().upper()
    valid = {code for code, _label in BROKER_ORDER}
    if value in valid:
        return value

    existing = set(
        Holding.objects.filter(user=user, broker__in=list(valid)).values_list("broker", flat=True)
    )
    for code, _label in BROKER_ORDER:
        if code in existing:
            return code
    return "RAKUTEN"


def _normalize_bucket(raw: Optional[str]) -> str:
    value = (raw or "").strip().lower()
    valid = {code for code, _label in BUCKET_ORDER}
    if value in valid:
        return value
    return "all"


def _build_subnav(active_key: str = "holdings"):
    items = [
        {"key": "holdings", "label": "保有", "url": reverse("holding_list")},
        {"key": "summary", "label": "サマリー", "url": reverse("holding_summary")},
        {"key": "attention", "label": "要注意", "url": None},
        {"key": "ai", "label": "AI提案", "url": None},
    ]
    for item in items:
        item["is_active"] = item["key"] == active_key
    return items


def _attach_row_flags(rows):
    for r in rows:
        h = r.obj
        r.can_margin_to_spot = (
            getattr(h, "account", "") == "MARGIN"
            and getattr(h, "side", "") == "BUY"
        )
    return rows


def _build_broker_panels(rows):
    broker_label_map = dict(BROKER_ORDER)
    account_label_map = dict(ACCOUNT_ORDER)

    panel_map = {}
    for broker_code, broker_label in BROKER_ORDER:
        panel_map[broker_code] = {
            "key": broker_code,
            "label": broker_label,
            "counts": {
                "all": 0,
                "spec": 0,
                "nisa": 0,
                "margin": 0,
            },
            "sections": {
                "SPEC": {
                    "key": "SPEC",
                    "label": account_label_map["SPEC"],
                    "count": 0,
                    "rows": [],
                },
                "NISA": {
                    "key": "NISA",
                    "label": account_label_map["NISA"],
                    "count": 0,
                    "rows": [],
                },
                "MARGIN": {
                    "key": "MARGIN",
                    "label": account_label_map["MARGIN"],
                    "count": 0,
                    "rows": [],
                },
            },
        }

    for r in rows:
        h = r.obj
        broker_code = getattr(h, "broker", "")
        account_code = getattr(h, "account", "")

        if broker_code not in panel_map:
            continue
        if account_code not in panel_map[broker_code]["sections"]:
            continue

        panel_map[broker_code]["sections"][account_code]["rows"].append(r)
        panel_map[broker_code]["sections"][account_code]["count"] += 1
        panel_map[broker_code]["counts"]["all"] += 1

        if account_code == "SPEC":
            panel_map[broker_code]["counts"]["spec"] += 1
        elif account_code == "NISA":
            panel_map[broker_code]["counts"]["nisa"] += 1
        elif account_code == "MARGIN":
            panel_map[broker_code]["counts"]["margin"] += 1

    panels = []
    for broker_code, broker_label in BROKER_ORDER:
        raw_panel = panel_map[broker_code]
        sections = []
        for account_code, _account_label in ACCOUNT_ORDER:
            sec = raw_panel["sections"][account_code]
            if sec["rows"]:
                sections.append(sec)

        panels.append(
            {
                "key": broker_code,
                "label": broker_label_map.get(broker_code, broker_code),
                "counts": raw_panel["counts"],
                "sections": sections,
                "has_rows": bool(raw_panel["counts"]["all"]),
            }
        )
    return panels


@login_required
def holding_list(request):
    active_broker = _normalize_broker(request.GET.get("broker"), request.user)
    active_bucket = _normalize_bucket(request.GET.get("bucket"))

    qs = (
        Holding.objects.filter(user=request.user)
        .prefetch_related("dividends")
        .order_by("broker", "account", "-updated_at", "-id")
    )

    rows = val.build_rows_for_queryset(qs)
    rows = _attach_row_flags(rows)

    broker_panels = _build_broker_panels(rows)

    broker_tabs = []
    for panel in broker_panels:
        broker_tabs.append(
            {
                "key": panel["key"],
                "label": panel["label"],
                "count": panel["counts"]["all"],
                "is_active": panel["key"] == active_broker,
            }
        )

    active_panel = next((x for x in broker_panels if x["key"] == active_broker), None)
    active_counts = active_panel["counts"] if active_panel else {"all": 0, "spec": 0, "nisa": 0, "margin": 0}

    bucket_tabs = []
    for key, label in BUCKET_ORDER:
        bucket_tabs.append(
            {
                "key": key,
                "label": label,
                "count": int(active_counts.get(key, 0)),
                "is_active": key == active_bucket,
            }
        )

    ctx = {
        "subnav_items": _build_subnav("holdings"),
        "broker_tabs": broker_tabs,
        "bucket_tabs": bucket_tabs,
        "broker_panels": broker_panels,
        "active_broker": active_broker,
        "active_bucket": active_bucket,
    }
    return render(request, "holdings/list.html", ctx)


@login_required
def holding_list_partial(request):
    return holding_list(request)