# =========================================================
# [FILE] sector33_service.py
# [PATH] <project_root>/tradeai/services/common/sector33_service.py
#
# このファイルは何？
# - tradeai 全体で使う「銘柄名 + 33業種セクター」の共通取得サービスです。
# - 日本語名を優先して取得し、セクターはJPX/TSEの33業種へ寄せます。
# - watchlist / watch_signals / holdings / dashboard から共通利用します。
# =========================================================

from __future__ import annotations

from typing import Any

import yfinance as yf
from django.conf import settings

from portfolio.models import Holding
from portfolio.services import trend as svc_trend
from tradeai.services.common.stock_lookup import lookup_stock_name_and_sector


SECTOR33_EXACT = {
    "水産・農林業",
    "鉱業",
    "建設業",
    "食料品",
    "繊維製品",
    "パルプ・紙",
    "化学",
    "医薬品",
    "石油・石炭製品",
    "ゴム製品",
    "ガラス・土石製品",
    "鉄鋼",
    "非鉄金属",
    "金属製品",
    "機械",
    "電気機器",
    "輸送用機器",
    "精密機器",
    "その他製品",
    "電気・ガス業",
    "陸運業",
    "海運業",
    "空運業",
    "倉庫・運輸関連業",
    "情報・通信業",
    "卸売業",
    "小売業",
    "銀行業",
    "証券、商品先物取引業",
    "保険業",
    "その他金融業",
    "不動産業",
    "サービス業",
}


def _to_symbol(raw_ticker: str) -> str:
    value = (raw_ticker or "").strip().upper()
    if value.endswith(".T"):
        return value
    if value.isdigit():
        return f"{value}.T"
    return value


def _base_ticker(raw_ticker: str) -> str:
    value = (raw_ticker or "").strip().upper()
    if value.endswith(".T"):
        return value[:-2]
    return value


def _normalize_text_for_match(value: str) -> str:
    return (
        (value or "")
        .strip()
        .lower()
        .replace("　", "")
        .replace(" ", "")
        .replace("・", "")
        .replace("/", "")
        .replace("(", "")
        .replace(")", "")
    )


def _contains_any(value: str, keywords: list[str]) -> bool:
    normalized = _normalize_text_for_match(value)
    return any(_normalize_text_for_match(keyword) in normalized for keyword in keywords)


def normalize_to_sector33(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return ""

    if text in SECTOR33_EXACT:
        return text

    alias_map = {
        "情報・通信": "情報・通信業",
        "情報通信": "情報・通信業",
        "電気ガス": "電気・ガス業",
        "電気・ガス": "電気・ガス業",
        "倉庫運輸関連": "倉庫・運輸関連業",
        "証券・商品先物取引": "証券、商品先物取引業",
        "証券商品先物取引業": "証券、商品先物取引業",
        "その他金融": "その他金融業",
        "不動産": "不動産業",
        "サービス": "サービス業",
        "小売": "小売業",
        "卸売": "卸売業",
        "銀行": "銀行業",
        "保険": "保険業",
        "陸運": "陸運業",
        "海運": "海運業",
        "空運": "空運業",
        "機械": "機械",
        "電気機器": "電気機器",
        "輸送用機器": "輸送用機器",
        "精密機器": "精密機器",
        "その他製品": "その他製品",
        "金属製品": "金属製品",
        "非鉄金属": "非鉄金属",
        "鉄鋼": "鉄鋼",
        "ガラス土石製品": "ガラス・土石製品",
        "ガラス・土石": "ガラス・土石製品",
        "石油石炭製品": "石油・石炭製品",
        "パルプ紙": "パルプ・紙",
        "繊維": "繊維製品",
        "食料": "食料品",
        "鉱業": "鉱業",
        "建設": "建設業",
        "化学": "化学",
        "医薬": "医薬品",
        "ゴム": "ゴム製品",
        "水産農林": "水産・農林業",
    }

    normalized = _normalize_text_for_match(text)
    for alias, mapped in alias_map.items():
        if _normalize_text_for_match(alias) == normalized:
            return mapped

    checks = [
        (["水産", "農林", "fishery", "agriculture", "forestry"], "水産・農林業"),
        (["mining", "鉱業"], "鉱業"),
        (["construction", "建設"], "建設業"),
        (["food", "beverage", "brew", "食品", "食料", "飲料"], "食料品"),
        (["textile", "apparel", "繊維"], "繊維製品"),
        (["paper", "pulp", "パルプ", "紙"], "パルプ・紙"),
        (["pharmaceutical", "drug", "biotech", "healthcare", "医薬"], "医薬品"),
        (["oil", "gas", "petroleum", "石油", "石炭"], "石油・石炭製品"),
        (["rubber", "タイヤ", "ゴム"], "ゴム製品"),
        (["glass", "ceramic", "cement", "ガラス", "土石"], "ガラス・土石製品"),
        (["steel", "鉄鋼"], "鉄鋼"),
        (["non-ferrous", "nonferrous", "aluminum", "copper", "非鉄"], "非鉄金属"),
        (["metalproduct", "metalfabrication", "金属製品"], "金属製品"),
        (["machinery", "industrialmachinery", "機械"], "機械"),
        (["semiconductor", "electronics", "electronic", "電機", "電気機器", "電子", "半導体", "家電"], "電気機器"),
        (["automobile", "autoparts", "transportationequipment", "aerospace", "defense", "shipbuilding", "輸送用機器", "自動車", "造船", "航空宇宙"], "輸送用機器"),
        (["precision", "medicaldevice", "精密"], "精密機器"),
        (["otherproducts", "furnishing", "toy", "music", "家具", "楽器", "その他製品"], "その他製品"),
        (["utilities", "utility", "electricpower", "電力", "ガス", "電気ガス"], "電気・ガス業"),
        (["rail", "railroad", "truck", "bus", "logistics", "陸運"], "陸運業"),
        (["shipping", "marine", "海運"], "海運業"),
        (["airline", "airtransport", "空運"], "空運業"),
        (["warehouse", "storage", "倉庫", "運輸関連"], "倉庫・運輸関連業"),
        (["telecom", "internet", "software", "itservices", "communicationservices", "media", "情報通信", "通信", "ソフトウェア", "インターネット"], "情報・通信業"),
        (["wholesale", "tradingcompany", "卸売", "商社"], "卸売業"),
        (["retail", "restaurant", "specialtyretail", "小売", "百貨店", "スーパー", "コンビニ", "外食"], "小売業"),
        (["bank", "banks", "銀行"], "銀行業"),
        (["brokerage", "securities", "commodityfutures", "証券", "商品先物"], "証券、商品先物取引業"),
        (["insurance", "保険"], "保険業"),
        (["consumerfinance", "creditservices", "leasing", "assetmanagement", "ノンバンク", "リース", "クレジット", "その他金融"], "その他金融業"),
        (["realestate", "reit", "不動産"], "不動産業"),
        (["chemical", "specialtychemicals", "basicmaterials", "materials", "chemicals", "化学"], "化学"),
        (["service", "services", "consulting", "staffing", "humanresources", "travelservices", "広告", "人材", "介護", "教育", "サービス"], "サービス業"),
    ]

    for keywords, mapped in checks:
        if _contains_any(text, keywords):
            return mapped

    return text


def _fetch_name_and_sector_from_lookup(ticker: str) -> dict[str, str]:
    code = _base_ticker(ticker)
    try:
        result = lookup_stock_name_and_sector(code)
    except Exception:
        return {"name": "", "sector": ""}

    return {
        "name": str((result or {}).get("name") or "").strip(),
        "sector": normalize_to_sector33(str((result or {}).get("sector") or "").strip()),
    }


def _fetch_name_and_sector_from_trend(ticker: str) -> dict[str, str]:
    code = _base_ticker(ticker)
    norm = _to_symbol(ticker)

    name = ""
    sector = ""

    override = getattr(settings, "TSE_NAME_OVERRIDES", {}).get(code)
    if override:
        name = override

    try:
        if code.isdigit():
            master_name, master_sector = svc_trend.lookup_master_name_and_sector(norm)
            if master_name and not name:
                name = master_name
            if master_sector and not sector:
                sector = master_sector
    except Exception:
        pass

    if not name:
        try:
            name = svc_trend._lookup_name_jp_from_list(norm) or ""
        except Exception:
            name = ""

    if not name:
        try:
            name = svc_trend._fetch_name_prefer_jp(norm) or ""
        except Exception:
            name = ""

    if not sector:
        try:
            sector = svc_trend._fetch_sector_prefer_jp(norm) or ""
        except Exception:
            sector = ""

    return {
        "name": str(name or "").strip(),
        "sector": normalize_to_sector33(str(sector or "").strip()),
    }


def _fetch_sector_from_yfinance(ticker: str) -> str:
    symbol = _to_symbol(ticker)
    try:
        info = yf.Ticker(symbol).get_info()
    except Exception:
        return ""

    if not info:
        return ""

    industry = str(info.get("industry") or "").strip()
    sector = str(info.get("sector") or "").strip()

    mapped = normalize_to_sector33(industry)
    if mapped:
        return mapped

    return normalize_to_sector33(sector)


def build_display_profile(
    ticker: str,
    fallback_name: str = "",
    fallback_sector: str = "",
) -> dict[str, str]:
    name = str(fallback_name or "").strip()
    sector = normalize_to_sector33(str(fallback_sector or "").strip())

    lookup_profile = _fetch_name_and_sector_from_lookup(ticker)
    if not name and lookup_profile["name"]:
        name = lookup_profile["name"]
    if not sector and lookup_profile["sector"]:
        sector = lookup_profile["sector"]

    trend_profile = _fetch_name_and_sector_from_trend(ticker)
    if not name and trend_profile["name"]:
        name = trend_profile["name"]
    if not sector and trend_profile["sector"]:
        sector = trend_profile["sector"]

    if not sector:
        sector = _fetch_sector_from_yfinance(ticker)

    return {
        "display_name": name or "銘柄名未取得",
        "sector_name": sector or "セクター未取得",
    }


def build_holding_sector_map(user) -> dict[str, str]:
    result: dict[str, str] = {}
    rows = Holding.objects.filter(user=user, quantity__gt=0).values("ticker", "sector")
    for row in rows:
        ticker = _base_ticker(str(row.get("ticker") or ""))
        if not ticker:
            continue
        result[ticker] = normalize_to_sector33(str(row.get("sector") or "").strip())
    return result


def enrich_model_items_with_profile(items: list[Any]) -> list[Any]:
    cache: dict[str, dict[str, str]] = {}

    for item in items:
        ticker = str(getattr(item, "ticker", "") or "")
        fallback_name = str(getattr(item, "name", "") or "").strip()

        if ticker not in cache:
            cache[ticker] = build_display_profile(
                ticker=ticker,
                fallback_name=fallback_name,
                fallback_sector="",
            )

        profile = cache[ticker]
        item.display_name = profile["display_name"]
        item.sector_name = profile["sector_name"]

    return items


def enrich_row_dicts_with_profile(
    rows: list[dict[str, Any]],
    sector_fallback_map: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    cache: dict[str, dict[str, str]] = {}
    sector_fallback_map = sector_fallback_map or {}

    for row in rows:
        ticker = str(row.get("ticker") or "")
        fallback_name = str(row.get("name") or "").strip()
        fallback_sector = sector_fallback_map.get(_base_ticker(ticker), "")

        if ticker not in cache:
            cache[ticker] = build_display_profile(
                ticker=ticker,
                fallback_name=fallback_name,
                fallback_sector=fallback_sector,
            )

        profile = cache[ticker]
        row["display_name"] = profile["display_name"]
        row["sector_name"] = profile["sector_name"]

    return rows