# =========================================================
# [FILE] stock_lookup.py
# [PATH] <project_root>/tradeai/services/common/stock_lookup.py
#
# このファイルは何？
# - 証券コードから銘柄名とセクターを引く共通サービスです。
# - holding の api_ticker_name と同じ考え方で、
#   tradeai 側でも再利用できるように独立させています。
# =========================================================

from __future__ import annotations

import time
from typing import Dict, Optional, Tuple

import yfinance as yf
from django.conf import settings

from portfolio.services import trend as svc_trend


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


def lookup_stock_name_and_sector(raw: str) -> dict:
    raw = (raw or "").strip()
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

    return {
        "code": code,
        "name": name,
        "sector": sector or "",
    }