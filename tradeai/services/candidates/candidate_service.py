# =========================================================
# [FILE] candidate_service.py
# [PATH] <project_root>/tradeai/services/candidates/candidate_service.py
#
# このファイルは何？
# - ユニバース全体から、ロング候補 / ショート候補を抽出するサービスです。
# - 地合い補正に加えて、保有/ウォッチ由来を少し優先します。
# - 候補ページで使う表示用データ
#   （100点満点換算、セクター、Entry/TP/SL、ピル表示、事実ベース根拠）
#   もここでまとめて作ります。
# - 今回は、セクター表示をJPX/TSEの33業種へ寄せるように修正しています。
# =========================================================

from __future__ import annotations

from typing import Any

import pandas as pd
import yfinance as yf
from django.conf import settings

from portfolio.models import Holding
from portfolio.services import trend as svc_trend
from tradeai.models.regime_snapshot import RegimeSnapshot
from tradeai.models.universe import UniverseTicker
from tradeai.services.indicators.atr_service import analyze_atr
from tradeai.services.indicators.breakout_service import analyze_breakout
from tradeai.services.indicators.gap_service import analyze_gap
from tradeai.services.indicators.ichimoku_service import analyze_ichimoku
from tradeai.services.indicators.ma_cross_service import analyze_ma_cross
from tradeai.services.indicators.ma_distance_service import analyze_ma_distance
from tradeai.services.indicators.macd_service import analyze_macd
from tradeai.services.indicators.rsi_service import analyze_rsi
from tradeai.services.indicators.volume_spike_service import analyze_volume_spike
from tradeai.services.indicators.vwap_service import analyze_vwap
from tradeai.services.indicators.wick_ratio_service import analyze_wick_ratio
from tradeai.services.regime.regime_service import ensure_recent_regime_snapshot
from tradeai.services.watchlist.monitor_service import (
    _big_flow_label,
    _breakout_label,
    _cost_position_label,
    _distance_human_label,
    _flow_label,
    _gap_human_label,
    _momentum_label,
    _score_long,
    _score_short,
    _timing_label,
    _volatility_label,
    _volume_label,
    _wick_human_label,
)


LEVEL_RANK = {
    "STRONG": 0,
    "ATTENTION": 1,
    "REFERENCE": 2,
}

MAX_SCORE_RAW = 28.0

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


def _safe_float(value):
    if value is None:
        return None
    try:
        return float(value)
    except Exception:
        return None


def _trim_text(text: str, max_len: int) -> str:
    safe = (text or "").strip()
    if len(safe) <= max_len:
        return safe
    return safe[: max_len - 1] + "…"


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


def _normalize_to_sector33(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return ""

    if text in SECTOR33_EXACT:
        return text

    # よくある日本語ブレを先に吸収
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


def _batch_download_ohlc(items: list[UniverseTicker], period: str = "6mo") -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    if not items:
        return result

    symbol_map = {item.ticker: _to_symbol(item.ticker) for item in items}
    symbols = list(dict.fromkeys(symbol_map.values()))
    if not symbols:
        return result

    try:
        data = yf.download(
            symbols,
            period=period,
            interval="1d",
            auto_adjust=False,
            progress=False,
            group_by="ticker",
            threads=True,
        )
    except Exception:
        return result

    if data is None or data.empty:
        return result

    for item in items:
        raw_ticker = item.ticker
        symbol = symbol_map.get(raw_ticker)
        if not symbol:
            continue

        try:
            if isinstance(data.columns, pd.MultiIndex):
                if symbol not in data.columns.get_level_values(0):
                    continue
                part = data[symbol].copy()
            else:
                part = data.copy()

            if part is None or part.empty:
                continue

            part = part.dropna(subset=["Open", "High", "Low", "Close"])
            if part.empty:
                continue

            opens = [float(v) for v in part["Open"].fillna(0).tolist()]
            highs = [float(v) for v in part["High"].fillna(0).tolist()]
            lows = [float(v) for v in part["Low"].fillna(0).tolist()]
            closes = [float(v) for v in part["Close"].fillna(0).tolist()]
            volumes = [float(v) for v in part["Volume"].fillna(0).tolist()] if "Volume" in part.columns else [0.0] * len(part)

            result[raw_ticker] = {
                "symbol": symbol,
                "opens": opens,
                "highs": highs,
                "lows": lows,
                "closes": closes,
                "volumes": volumes,
                "last_open": opens[-1] if opens else None,
                "last_close": closes[-1] if closes else None,
                "last_high": highs[-1] if highs else None,
                "last_low": lows[-1] if lows else None,
                "last_volume": volumes[-1] if volumes else None,
            }
        except Exception:
            continue

    return result


def _build_technical_snapshot_from_ohlc(ohlc: dict[str, Any] | None) -> dict[str, Any]:
    if not ohlc:
        return {
            "has_technical": False,
            "last_close": None,
            "ma_state": "UNKNOWN",
            "ma_label": "不明",
            "short_ma": None,
            "long_ma": None,
            "macd_state": "UNKNOWN",
            "macd_label": "不明",
            "rsi_state": "UNKNOWN",
            "rsi_label": "不明",
            "rsi": None,
            "atr": None,
            "atr_pct": None,
            "vwap": None,
            "vwap_state": "UNKNOWN",
            "vwap_label": "不明",
            "volume_ratio": None,
            "volume_state": "UNKNOWN",
            "volume_label": "不明",
            "breakout_state": "UNKNOWN",
            "breakout_label": "不明",
            "range_high": None,
            "range_low": None,
            "ichimoku_state": "UNKNOWN",
            "ichimoku_label": "不明",
            "tenkan": None,
            "kijun": None,
            "span_a": None,
            "span_b": None,
            "ma_distance_state": "UNKNOWN",
            "ma_distance_label": "不明",
            "ma_value": None,
            "distance_pct": None,
            "wick_state": "UNKNOWN",
            "wick_label": "不明",
            "upper_wick_pct": None,
            "lower_wick_pct": None,
            "gap_state": "UNKNOWN",
            "gap_label": "不明",
            "gap_pct": None,
        }

    ma_info = analyze_ma_cross(ohlc["closes"], short_window=5, long_window=25)
    macd_info = analyze_macd(ohlc["closes"])
    rsi_info = analyze_rsi(ohlc["closes"], period=14)
    atr_info = analyze_atr(ohlc["highs"], ohlc["lows"], ohlc["closes"], period=14)
    vwap_info = analyze_vwap(ohlc["closes"], ohlc["volumes"], lookback=20)
    volume_info = analyze_volume_spike(ohlc["volumes"], lookback=20)
    breakout_info = analyze_breakout(ohlc["highs"], ohlc["lows"], ohlc["closes"], lookback=20)
    ichimoku_info = analyze_ichimoku(ohlc["highs"], ohlc["lows"], ohlc["closes"])
    ma_distance_info = analyze_ma_distance(ohlc["closes"], window=25)
    wick_info = analyze_wick_ratio(ohlc["opens"], ohlc["highs"], ohlc["lows"], ohlc["closes"])
    gap_info = analyze_gap(ohlc["opens"], ohlc["closes"])

    return {
        "has_technical": True,
        "last_close": ohlc["last_close"],
        **ma_info,
        **macd_info,
        **rsi_info,
        **atr_info,
        **vwap_info,
        **volume_info,
        **breakout_info,
        **ichimoku_info,
        **ma_distance_info,
        **wick_info,
        **gap_info,
    }


def _pick_direction(long_score: int, short_score: int, tech: dict[str, Any]) -> str | None:
    if long_score > short_score:
        return "LONG"
    if short_score > long_score:
        return "SHORT"

    if tech.get("breakout_state") == "HIGH_BREAKOUT":
        return "LONG"
    if tech.get("breakout_state") == "LOW_BREAKDOWN":
        return "SHORT"

    if tech.get("ma_state") in ("GOLDEN_CROSS", "ABOVE_GC"):
        return "LONG"
    if tech.get("ma_state") in ("DEAD_CROSS", "BELOW_DC"):
        return "SHORT"

    return None


def _level_from_score(score: int) -> str:
    if score >= 13:
        return "STRONG"
    if score >= 9:
        return "ATTENTION"
    return "REFERENCE"


def _source_labels(item: UniverseTicker) -> list[str]:
    labels: list[str] = []
    if item.from_holding:
        labels.append("保有")
    if item.from_watchlist:
        labels.append("ウォッチ")
    if item.in_nikkei225:
        labels.append("日経225")
    if item.in_topix:
        labels.append("TOPIX")
    return labels


def _source_rank(item: UniverseTicker) -> int:
    if item.from_holding:
        return 0
    if item.from_watchlist:
        return 1
    if item.in_nikkei225:
        return 2
    if item.in_topix:
        return 3
    return 4


def _source_bonus(item: UniverseTicker) -> int:
    bonus = 0
    if item.from_holding:
        bonus += 2
    if item.from_watchlist:
        bonus += 1
    return bonus


def _regime_adjustments(snapshot: RegimeSnapshot | None) -> tuple[int, int]:
    if not snapshot:
        return 0, 0

    if snapshot.market_bias == RegimeSnapshot.MarketBias.LONG_TAILWIND:
        return 2, -1
    if snapshot.market_bias == RegimeSnapshot.MarketBias.SHORT_TAILWIND:
        return -1, 2
    if snapshot.market_bias == RegimeSnapshot.MarketBias.NOISY:
        return -1, -1
    return 0, 0


def _regime_relation_label(direction: str | None, snapshot: RegimeSnapshot | None) -> str:
    if not snapshot or not direction:
        return "地合い中立"

    if snapshot.market_bias == RegimeSnapshot.MarketBias.NOISY:
        return "地合い荒れ気味"

    if direction == "LONG" and snapshot.market_bias == RegimeSnapshot.MarketBias.LONG_TAILWIND:
        return "地合い追い風"
    if direction == "SHORT" and snapshot.market_bias == RegimeSnapshot.MarketBias.SHORT_TAILWIND:
        return "地合い追い風"
    if direction == "LONG" and snapshot.market_bias == RegimeSnapshot.MarketBias.SHORT_TAILWIND:
        return "地合い逆風"
    if direction == "SHORT" and snapshot.market_bias == RegimeSnapshot.MarketBias.LONG_TAILWIND:
        return "地合い逆風"

    return "地合い中立"


def _score_to_100(score_total: int) -> int:
    bounded = max(0.0, min(float(score_total), MAX_SCORE_RAW))
    return int(round((bounded / MAX_SCORE_RAW) * 100))


def _format_price(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{float(value):.2f}"


def _profile_map_from_holdings(user) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    rows = Holding.objects.filter(user=user, quantity__gt=0).values("ticker", "name", "sector")
    for row in rows:
        ticker = _base_ticker(str(row.get("ticker") or ""))
        if not ticker:
            continue
        result[ticker] = {
            "name": str(row.get("name") or "").strip(),
            "sector": _normalize_to_sector33(str(row.get("sector") or "").strip()),
        }
    return result


def _fetch_profile_from_trend(ticker: str) -> dict[str, str]:
    """
    watchlist / holding と同じ方向で、日本語名を優先取得する。
    セクターは最終的に33業種へ寄せる。
    """
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

    sector = _normalize_to_sector33(sector)

    return {
        "name": str(name or "").strip(),
        "sector": str(sector or "").strip(),
    }


def _fetch_profile_from_yfinance(ticker: str) -> dict[str, str]:
    symbol = _to_symbol(ticker)
    try:
        info = yf.Ticker(symbol).get_info()
    except Exception:
        return {"name": "", "sector": ""}

    if not info:
        return {"name": "", "sector": ""}

    name = (
        str(info.get("longName") or "")
        or str(info.get("shortName") or "")
        or str(info.get("displayName") or "")
    ).strip()

    industry = str(info.get("industry") or "").strip()
    sector = str(info.get("sector") or "").strip()

    mapped_sector = _normalize_to_sector33(industry)
    if not mapped_sector:
        mapped_sector = _normalize_to_sector33(sector)

    return {"name": name, "sector": mapped_sector}


def _fetch_profile_jp_first(ticker: str) -> dict[str, str]:
    profile = _fetch_profile_from_trend(ticker)

    if profile.get("name") and profile.get("sector"):
        return profile

    fallback = _fetch_profile_from_yfinance(ticker)

    if not profile.get("name"):
        profile["name"] = fallback.get("name", "")
    if not profile.get("sector"):
        profile["sector"] = fallback.get("sector", "")

    profile["sector"] = _normalize_to_sector33(profile.get("sector", ""))
    return profile


def _enrich_profiles_for_rows(user, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return

    holding_map = _profile_map_from_holdings(user)
    cache: dict[str, dict[str, str]] = {}

    for row in rows:
        ticker = str(row.get("ticker") or "")
        base = _base_ticker(ticker)

        profile = holding_map.get(base, {}).copy()
        if not profile.get("name"):
            profile["name"] = str(row.get("name") or "").strip()
        if not profile.get("sector"):
            profile["sector"] = ""

        if (not profile.get("name") or not profile.get("sector")) and base not in cache:
            cache[base] = _fetch_profile_jp_first(ticker)

        fetched = cache.get(base, {})
        if not profile.get("name"):
            profile["name"] = fetched.get("name", "")
        if not profile.get("sector"):
            profile["sector"] = fetched.get("sector", "")

        row["display_name"] = profile.get("name") or (row.get("name") or "") or "銘柄名未取得"
        row["sector_name"] = _normalize_to_sector33(profile.get("sector", "")) or "セクター未取得"


def _count_universe_sources(items: list[UniverseTicker]) -> dict[str, int]:
    return {
        "holding": sum(1 for item in items if item.from_holding),
        "watchlist": sum(1 for item in items if item.from_watchlist),
        "nikkei225": sum(1 for item in items if item.in_nikkei225),
        "topix": sum(1 for item in items if item.in_topix),
    }


def _count_candidate_sources(rows: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "holding": sum(1 for row in rows if "保有" in row.get("source_labels", [])),
        "watchlist": sum(1 for row in rows if "ウォッチ" in row.get("source_labels", [])),
        "nikkei225": sum(1 for row in rows if "日経225" in row.get("source_labels", [])),
        "topix": sum(1 for row in rows if "TOPIX" in row.get("source_labels", [])),
    }


def _build_trade_plan(direction: str | None, tech: dict[str, Any]) -> tuple[float | None, float | None, float | None]:
    last_close = _safe_float(tech.get("last_close"))
    atr = _safe_float(tech.get("atr"))
    range_high = _safe_float(tech.get("range_high"))
    range_low = _safe_float(tech.get("range_low"))
    breakout_state = str(tech.get("breakout_state") or "")

    if last_close is None:
        return None, None, None

    if atr is None or atr <= 0:
        atr = last_close * 0.02

    risk_width = max(atr * 1.2, last_close * 0.015)

    if direction == "LONG":
        if breakout_state in ("HIGH_BREAKOUT", "NEAR_HIGH") and range_high is not None:
            entry = max(last_close, range_high)
        else:
            entry = last_close
        sl = entry - risk_width
        tp = entry + (risk_width * 2.0)
        return round(entry, 2), round(tp, 2), round(sl, 2)

    if direction == "SHORT":
        if breakout_state in ("LOW_BREAKDOWN", "NEAR_LOW") and range_low is not None:
            entry = min(last_close, range_low)
        else:
            entry = last_close
        sl = entry + risk_width
        tp = entry - (risk_width * 2.0)
        return round(entry, 2), round(tp, 2), round(sl, 2)

    return round(last_close, 2), None, None


def _build_indicator_pills(tech: dict[str, Any]) -> list[str]:
    last_close = _safe_float(tech.get("last_close"))
    atr_pct = _safe_float(tech.get("atr_pct"))

    pills: list[str] = []

    if last_close is not None:
        pills.append(f"現在値 {last_close:.2f}")

    pills.append(f"流れ(MA) {_flow_label(str(tech.get('ma_state') or ''))}")
    pills.append(f"勢い(MACD) {_momentum_label(str(tech.get('macd_state') or ''))}")
    pills.append(f"大流れ(一目) {_big_flow_label(str(tech.get('ichimoku_label') or ''))}")
    pills.append(f"節目(20日高安) {_breakout_label(str(tech.get('breakout_label') or ''))}")

    if atr_pct is not None:
        pills.append(f"値動き(ATR) {atr_pct:.2f}%")
    else:
        pills.append(f"値動き(ATR) {_volatility_label(None)}")

    pills.append(f"位置(VWAP) {_cost_position_label(str(tech.get('vwap_state') or ''))}")

    return pills


def _build_fact_reasons(direction: str | None, tech: dict[str, Any]) -> list[str]:
    reasons: list[str] = []

    last_close = _safe_float(tech.get("last_close"))
    short_ma = _safe_float(tech.get("short_ma"))
    long_ma = _safe_float(tech.get("long_ma"))
    vwap = _safe_float(tech.get("vwap"))
    range_high = _safe_float(tech.get("range_high"))
    range_low = _safe_float(tech.get("range_low"))
    atr = _safe_float(tech.get("atr"))
    atr_pct = _safe_float(tech.get("atr_pct"))
    rsi = _safe_float(tech.get("rsi"))
    volume_ratio = _safe_float(tech.get("volume_ratio"))

    ma_state = str(tech.get("ma_state") or "")
    breakout_state = str(tech.get("breakout_state") or "")
    ichimoku_label = str(tech.get("ichimoku_label") or "")
    macd_state = str(tech.get("macd_state") or "")
    vwap_state = str(tech.get("vwap_state") or "")

    if direction == "LONG":
        if short_ma is not None and long_ma is not None and ma_state in ("GOLDEN_CROSS", "ABOVE_GC"):
            reasons.append(f"5日線 {_format_price(short_ma)} が 25日線 {_format_price(long_ma)} を上回る")

        if last_close is not None and vwap is not None and vwap_state in ("CROSS_UP", "ABOVE_VWAP"):
            reasons.append(f"終値 {_format_price(last_close)} が VWAP {_format_price(vwap)} より上")

        if last_close is not None and range_high is not None:
            if breakout_state == "HIGH_BREAKOUT":
                reasons.append(f"終値 {_format_price(last_close)} が 20日高値 {_format_price(range_high)} を上抜け")
            elif breakout_state == "NEAR_HIGH":
                diff_pct = ((range_high - last_close) / last_close * 100.0) if last_close else 0.0
                reasons.append(f"20日高値 {_format_price(range_high)} まで あと {diff_pct:.2f}%")

        if ichimoku_label in ("雲の上で上向き", "雲の上"):
            reasons.append(f"一目が {ichimoku_label}")

        if macd_state in ("BULLISH_CROSS", "BULLISH_ABOVE"):
            reasons.append(f"MACD が {_momentum_label(macd_state)}")

    elif direction == "SHORT":
        if short_ma is not None and long_ma is not None and ma_state in ("DEAD_CROSS", "BELOW_DC"):
            reasons.append(f"5日線 {_format_price(short_ma)} が 25日線 {_format_price(long_ma)} を下回る")

        if last_close is not None and vwap is not None and vwap_state in ("CROSS_DOWN", "BELOW_VWAP"):
            reasons.append(f"終値 {_format_price(last_close)} が VWAP {_format_price(vwap)} より下")

        if last_close is not None and range_low is not None:
            if breakout_state == "LOW_BREAKDOWN":
                reasons.append(f"終値 {_format_price(last_close)} が 20日安値 {_format_price(range_low)} を下抜け")
            elif breakout_state == "NEAR_LOW":
                diff_pct = ((last_close - range_low) / last_close * 100.0) if last_close else 0.0
                reasons.append(f"20日安値 {_format_price(range_low)} まで あと {diff_pct:.2f}%")

        if ichimoku_label in ("雲の下で下向き", "雲の下"):
            reasons.append(f"一目が {ichimoku_label}")

        if macd_state in ("BEARISH_CROSS", "BEARISH_BELOW"):
            reasons.append(f"MACD が {_momentum_label(macd_state)}")

    if atr is not None and atr_pct is not None:
        reasons.append(f"ATR {_format_price(atr)}円 / {atr_pct:.2f}%")

    if volume_ratio is not None and volume_ratio >= 1.2:
        reasons.append(f"出来高が {volume_ratio:.2f}倍")

    if rsi is not None:
        reasons.append(f"RSI {rsi:.1f}（{_timing_label(str(tech.get('rsi_label') or ''))}）")

    unique_reasons: list[str] = []
    for reason in reasons:
        if reason not in unique_reasons:
            unique_reasons.append(reason)

    return unique_reasons[:4]


def _decision_text(direction: str | None, level: str, row: dict[str, Any], snapshot: RegimeSnapshot | None) -> str:
    regime_relation = _regime_relation_label(direction, snapshot)
    breakout_label = row.get("breakout_human_label") or ""
    distance_label = row.get("distance_human_label") or ""
    volatility_label = row.get("volatility_label") or ""

    if direction == "LONG":
        if regime_relation == "地合い追い風":
            if breakout_label in ("高値ぬけ", "高値に近い"):
                text = "地合いも追い風なので、上抜け確認候補として見やすいです。"
            else:
                text = "個別も地合いもロング寄りなので、継続確認向きです。"
        elif regime_relation == "地合い逆風":
            text = "個別は強めですが、地合いは逆風なので飛びつきすぎは注意です。"
        elif regime_relation == "地合い荒れ気味":
            text = "ロング寄りですが、地合いが荒れ気味なので無理は禁物です。"
        else:
            text = "ロング寄りですが、地合いは中立なので個別の強さ確認が大事です。"
    elif direction == "SHORT":
        if regime_relation == "地合い追い風":
            if breakout_label in ("安値わり", "安値に近い"):
                text = "地合いも追い風なので、下抜け確認候補として見やすいです。"
            else:
                text = "個別も地合いもショート寄りなので、継続確認向きです。"
        elif regime_relation == "地合い逆風":
            text = "個別は弱めですが、地合いは逆風なので売り急ぎは注意です。"
        elif regime_relation == "地合い荒れ気味":
            text = "ショート寄りですが、地合いが荒れ気味なので無理は禁物です。"
        else:
            text = "ショート寄りですが、地合いは中立なので個別の弱さ確認が大事です。"
    else:
        text = "まだ強い一方向ではなく、今は様子見寄りです。"

    if distance_label in ("かなり上がりすぎ", "かなり下がりすぎ"):
        text += " 行きすぎ感もあるので、追いかけすぎは注意です。"
    elif volatility_label == "大きい":
        text += " 値動きが大きいので、位置取りは慎重にしたいです。"

    return _trim_text(text, 90)


def build_candidate_rows(user, limit_per_side: int = 8) -> dict[str, Any]:
    last_regime = ensure_recent_regime_snapshot(user=user, max_age_minutes=90)

    universe_items = list(
        UniverseTicker.objects.filter(user=user, is_active=True).order_by("priority", "ticker")
    )

    ohlc_map = _batch_download_ohlc(universe_items, period="6mo")
    long_adj, short_adj = _regime_adjustments(last_regime)

    universe_source_counts = _count_universe_sources(universe_items)

    all_rows: list[dict[str, Any]] = []

    for item in universe_items:
        ohlc = ohlc_map.get(item.ticker)
        tech = _build_technical_snapshot_from_ohlc(ohlc)
        if not tech.get("has_technical"):
            continue

        base_long_score, long_reasons = _score_long(tech)
        base_short_score, short_reasons = _score_short(tech)
        source_bonus = _source_bonus(item)

        long_score = base_long_score + long_adj + source_bonus
        short_score = base_short_score + short_adj + source_bonus

        chosen_direction = _pick_direction(long_score, short_score, tech)
        score_total = max(long_score, short_score)
        level = _level_from_score(score_total)

        if not chosen_direction or level == "REFERENCE":
            continue

        selected_reasons = long_reasons if chosen_direction == "LONG" else short_reasons
        fact_reasons = _buildFact_reasons(chosen_direction, tech) if False else _build_fact_reasons(chosen_direction, tech)
        entry_price, tp_price, sl_price = _build_trade_plan(chosen_direction, tech)

        if chosen_direction == "LONG":
            action_label = "強いロング候補" if level == "STRONG" else "ロング候補"
        else:
            action_label = "強いショート候補" if level == "STRONG" else "ショート候補"

        row = {
            "ticker": item.ticker,
            "name": item.name or "",
            "priority": item.priority,
            "source_labels": _source_labels(item),
            "source_rank": _source_rank(item),
            "source_bonus": source_bonus,
            "chosen_direction": chosen_direction,
            "level": level,
            "action_label": action_label,
            "score_total": score_total,
            "score_100": _score_to_100(score_total),
            "base_long_score": base_long_score,
            "base_short_score": base_short_score,
            "long_score": long_score,
            "short_score": short_score,
            "last_close": tech["last_close"],
            "entry_price": entry_price,
            "tp_price": tp_price,
            "sl_price": sl_price,
            "flow_label": _flow_label(tech["ma_state"]),
            "momentum_label": _momentum_label(tech["macd_state"]),
            "timing_label": _timing_label(tech["rsi_label"]),
            "volatility_label": _volatility_label(tech["atr_pct"]),
            "cost_position_label": _cost_position_label(tech["vwap_state"]),
            "volume_human_label": _volume_label(tech["volume_label"]),
            "breakout_human_label": _breakout_label(tech["breakout_label"]),
            "big_flow_label": _big_flow_label(tech["ichimoku_label"]),
            "distance_human_label": _distance_human_label(tech["ma_distance_label"]),
            "wick_human_label": _wick_human_label(tech["wick_label"]),
            "gap_human_label": _gap_human_label(tech["gap_label"]),
            "selected_reasons": selected_reasons,
            "fact_reasons": fact_reasons,
            "regime_relation_label": _regime_relation_label(chosen_direction, last_regime),
            "indicator_pills": _build_indicator_pills(tech),
        }
        row["decision_text"] = _decision_text(chosen_direction, level, row, last_regime)
        all_rows.append(row)

    all_rows.sort(
        key=lambda row: (
            LEVEL_RANK.get(row["level"], 9),
            -(row["score_total"]),
            row["source_rank"],
            row["priority"],
            row["ticker"],
        )
    )

    long_rows_all = [row for row in all_rows if row["chosen_direction"] == "LONG"]
    short_rows_all = [row for row in all_rows if row["chosen_direction"] == "SHORT"]

    long_rows = long_rows_all[:limit_per_side]
    short_rows = short_rows_all[:limit_per_side]
    display_rows = long_rows + short_rows
    _enrich_profiles_for_rows(user, display_rows)

    candidate_source_counts = _count_candidate_sources(all_rows)

    return {
        "last_regime": last_regime,
        "score_max": 100,
        "universe_count": len(universe_items),
        "candidate_total": len(all_rows),
        "long_candidate_count": len(long_rows_all),
        "short_candidate_count": len(short_rows_all),
        "strong_candidate_count": sum(1 for row in all_rows if row["level"] == "STRONG"),
        "attention_candidate_count": sum(1 for row in all_rows if row["level"] == "ATTENTION"),
        "universe_source_counts": universe_source_counts,
        "candidate_source_counts": candidate_source_counts,
        "long_rows": long_rows,
        "short_rows": short_rows,
        "all_rows": all_rows,
    }