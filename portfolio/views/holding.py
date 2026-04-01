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
from django.http import JsonResponse
from django.shortcuts import render

from ..services import trend as svc_trend
from ..services.holdings import list_service as list_svc


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


@login_required
def holding_list(request):
    ctx = list_svc.build_holdings_list_context(
        user=request.user,
        broker=request.GET.get("broker"),
        bucket=request.GET.get("bucket"),
    )
    return render(request, "holdings/list.html", ctx)