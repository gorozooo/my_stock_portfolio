# [FILE] holding.py
# [PATH] portfolio/views/holding.py
#
# このファイルは何？
# - Holding 一覧表示と銘柄APIだけを担当する view
#
# 今回の目的
# - 評価・集計・スパーク・配当利回り計算は service へ分離
# - create/edit/delete は holding_actions.py 側に分離済み
# - このファイルは「一覧表示」と「ticker→銘柄名API」に集中する

# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Dict, Optional, Tuple
import time

import yfinance as yf
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import models
from django.http import JsonResponse
from django.shortcuts import render

from ..models import Holding
from ..services import trend as svc_trend
from ..services.holdings import valuation_service as val

SECTOR_CACHE_TTL = 30 * 60
_SECTOR_CACHE: Dict[str, Tuple[float, str]] = {}


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


def _apply_filters(qs, request):
    def _normalize_choice(field_name: str, raw: str) -> Optional[str]:
        if raw is None:
            return None
        s = str(raw).strip()
        if s == "" or s.upper() == "ALL" or s == "すべて":
            return None

        import unicodedata as _ud
        key = _ud.normalize("NFKC", s).strip()

        field = Holding._meta.get_field(field_name)
        for value, label in (field.choices or []):
            v = str(value)
            l = _ud.normalize("NFKC", str(label)).strip()
            if key == v or key == l:
                return value
        return s

    broker = _normalize_choice("broker", request.GET.get("broker"))
    account = _normalize_choice("account", request.GET.get("account"))
    side = _normalize_choice("side", request.GET.get("side"))

    if broker:
        qs = qs.filter(broker=broker)
    if account:
        qs = qs.filter(account=account)
    if side:
        qs = qs.filter(side=side)

    q = (request.GET.get("q") or request.GET.get("ticker") or "").strip()
    if q:
        qs = qs.filter(models.Q(ticker__icontains=q) | models.Q(name__icontains=q))

    return qs


def _sort_qs(qs, request):
    sort = request.GET.get("sort") or "updated"
    order = request.GET.get("order") or "desc"
    if sort in ("updated", "created", "opened"):
        field = {"updated": "updated_at", "created": "created_at", "opened": "opened_at"}[sort]
        if order == "asc":
            qs = qs.order_by(field, "-id")
        else:
            qs = qs.order_by(f"-{field}", "-id")
    else:
        qs = qs.order_by("-updated_at", "-id")
    return qs


def _page(request, qs, per_page: int = 10):
    p = int(request.GET.get("page") or 1)
    paginator = Paginator(qs, per_page)
    return paginator.get_page(p)


class _PageWrap:
    def __init__(self, src, objs):
        self.number = src.number
        self.paginator = src.paginator
        self.has_previous = src.has_previous
        self.has_next = src.has_next
        self.previous_page_number = src.previous_page_number
        self.next_page_number = src.next_page_number
        self.object_list = objs


@login_required
def holding_list(request):
    qs = Holding.objects.filter(user=request.user).prefetch_related("dividends")
    qs = _apply_filters(qs, request)
    qs = _sort_qs(qs, request)

    page = _page(request, qs)
    rows_page = val.build_rows_for_page(page)
    rows_page = val.apply_post_filters(rows_page, request)
    rows_page = val.sort_rows(rows_page, request)

    rows_all = val.build_rows_for_queryset(qs)
    rows_all = val.apply_post_filters(rows_all, request)
    summary = val.aggregate_rows(rows_all)
    summary["count"] = qs.count()
    summary["page_count"] = len(rows_page)

    page_wrap = _PageWrap(page, rows_page)

    ctx = {
        "page": page_wrap,
        "sort": request.GET.get("sort") or "updated",
        "order": request.GET.get("order") or "desc",
        "filters": {
            "broker": request.GET.get("broker") or "",
            "account": request.GET.get("account") or "",
            "ticker": request.GET.get("ticker") or "",
            "side": request.GET.get("side") or "",
            "pnl": request.GET.get("pnl") or "",
        },
        "summary": summary,
    }
    return render(request, "holdings/list.html", ctx)


@login_required
def holding_list_partial(request):
    qs = Holding.objects.filter(user=request.user).prefetch_related("dividends")
    qs = _apply_filters(qs, request)
    qs = _sort_qs(qs, request)

    page = _page(request, qs)
    rows_page = val.build_rows_for_page(page)
    rows_page = val.apply_post_filters(rows_page, request)
    rows_page = val.sort_rows(rows_page, request)

    rows_all = val.build_rows_for_queryset(qs)
    rows_all = val.apply_post_filters(rows_all, request)
    summary = val.aggregate_rows(rows_all)
    summary["count"] = qs.count()
    summary["page_count"] = len(rows_page)

    page_wrap = _PageWrap(page, rows_page)

    ctx = {
        "page": page_wrap,
        "sort": request.GET.get("sort") or "updated",
        "order": request.GET.get("order") or "desc",
        "filters": {
            "broker": request.GET.get("broker") or "",
            "account": request.GET.get("account") or "",
            "ticker": request.GET.get("ticker") or "",
            "side": request.GET.get("side") or "",
            "pnl": request.GET.get("pnl") or "",
        },
        "summary": summary,
    }
    return render(request, "holdings/_list.html", ctx)