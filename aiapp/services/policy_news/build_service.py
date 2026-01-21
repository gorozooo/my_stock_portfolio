# aiapp/services/policy_news/build_service.py
# -*- coding: utf-8 -*-
"""
policy_news（ニュース/政策/社会情勢）の“生成”サービス（build層）

A案（人間更新ゼロ）:
- input_policy_news.json は使わない（完全自動）
- fundamentals（市場データ）からイベントを自動検出して items を生成
- repo の集計ロジックで factors_sum / sector_sum を付与した snapshot を出力

イベントは「定量条件 + 固定マップ」で決める（再現性ファースト）。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .schema import PolicyNewsItem, PolicyNewsSnapshot
from .repo import dump_policy_news_snapshot, load_policy_news_snapshot
from .settings import JST, POLICY_NEWS_DIR, LATEST_POLICY_NEWS, dt_now_stamp

FUND_LATEST = Path("media/aiapp/fundamentals/latest_fundamentals.json")


def _safe_json_load(path: Path) -> Dict[str, Any]:
    try:
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _safe_float(x) -> Optional[float]:
    try:
        if x is None:
            return None
        v = float(x)
        if v != v:
            return None
        return v
    except Exception:
        return None


def _get_series_item(fund: Dict[str, Any], sym: str) -> Dict[str, Any]:
    mc = fund.get("market_context") or {}
    series = mc.get("series") or {}
    v = series.get(sym)
    return v if isinstance(v, dict) else {}


def _extract_fund_asof_date(fund: Dict[str, Any]) -> str:
    iso = None
    try:
        iso = (fund.get("meta") or {}).get("asof")
    except Exception:
        iso = None
    if isinstance(iso, str) and len(iso) >= 10:
        return iso[:10]
    return datetime.now(JST).strftime("%Y-%m-%d")


def _norm_text(s: Any) -> str:
    return str(s or "").strip()


def _mk_item(
    *,
    _id: str,
    title: str,
    category: str,
    sectors: List[str],
    factors: Dict[str, float],
    sector_delta: Dict[str, float],
    reason: str,
    extra: Optional[Dict[str, Any]] = None,
) -> PolicyNewsItem:
    return PolicyNewsItem(
        id=_id,
        title=title,
        category=category,
        sectors=[_norm_text(x) for x in sectors if _norm_text(x)],
        factors={k: float(v) for k, v in (factors or {}).items()},
        sector_delta={_norm_text(k): float(v) for k, v in (sector_delta or {}).items() if _norm_text(k)},
        reason=reason,
        source="auto_market",
        url=None,
        extra=extra or {},
    )


# =========================
# 固定マップ（再現性の核）
# =========================
# ここは「毎日人間が触る場所」ではない。
# ルールとしてコード管理し、将来はログ学習で調整する。
EVENT_LIBRARY: Dict[str, Dict[str, Any]] = {
    # 円安（輸出寄り）
    "yen_weak": {
        "title": "円安方向",
        "category": "fx",
        "sectors": ["輸送用機器", "電気機器", "機械", "精密機器", "化学"],
        "factors": {"fx": +0.8, "rates": 0.0, "risk": 0.0},
        "sector_delta_each": +0.12,
    },
    # 円高（逆風）
    "yen_strong": {
        "title": "円高方向",
        "category": "fx",
        "sectors": ["輸送用機器", "電気機器", "機械", "精密機器", "化学"],
        "factors": {"fx": -0.8, "rates": 0.0, "risk": 0.0},
        "sector_delta_each": -0.12,
    },
    # リスクオフ（ディフェンシブ）
    "risk_off": {
        "title": "リスクオフ寄り",
        "category": "risk",
        "sectors": ["医薬品", "食料品", "電気・ガス業"],
        "factors": {"fx": 0.0, "rates": 0.0, "risk": +0.7},
        "sector_delta_each": +0.21,
    },
    # リスクオン（景気敏感）
    "risk_on": {
        "title": "リスクオン寄り",
        "category": "risk",
        "sectors": ["鉄鋼", "非鉄金属", "海運業", "機械", "電気機器", "輸送用機器"],
        "factors": {"fx": 0.0, "rates": 0.0, "risk": -0.7},
        "sector_delta_each": +0.12,
    },
    # 金利上昇（株式のグロース逆風っぽい扱い）
    "rates_up": {
        "title": "金利上昇寄り",
        "category": "rates",
        "sectors": ["銀行業", "保険業", "不動産業", "情報・通信業"],
        "factors": {"fx": 0.0, "rates": +0.6, "risk": 0.0},
        "sector_delta_each": +0.10,
    },
    # 金利低下
    "rates_down": {
        "title": "金利低下寄り",
        "category": "rates",
        "sectors": ["不動産業", "情報・通信業", "サービス業"],
        "factors": {"fx": 0.0, "rates": -0.6, "risk": 0.0},
        "sector_delta_each": +0.10,
    },
}


# =========================
# 検出ルール（定量）
# =========================
# ここも毎日いじらない。ルールとして固定。
THRESHOLDS = {
    "usd_jpy_pct": 0.35,   # 1日で±0.35%超
    "dxy_pct": 0.30,       # 1日で±0.30%超（簡易リスク指標として）
    "tnx_pct": 0.60,       # ^TNX change_pct（供給元によりスケール差が出やすいのでやや広め）
    "jgb10y_level_hi": 1.50,
    "jgb10y_level_lo": 0.70,
}


def build_policy_news_snapshot(*, asof: Optional[str] = None, source: str = "auto_market") -> PolicyNewsSnapshot:
    """
    policy_news snapshot を「完全自動」で生成する。

    入力:
    - media/aiapp/fundamentals/latest_fundamentals.json

    出力:
    - items: 市場イベント（円安/円高、リスクオン/オフ、金利上昇/低下）
    - repo集計により factors_sum / sector_sum が付与される
    """
    POLICY_NEWS_DIR.mkdir(parents=True, exist_ok=True)

    fund = _safe_json_load(FUND_LATEST)
    asof2 = str(asof or _extract_fund_asof_date(fund))

    # 市場入力
    usd_jpy_pct = _safe_float(_get_series_item(fund, "USDJPY=X").get("change_pct"))
    dxy_pct = _safe_float(_get_series_item(fund, "DX-Y.NYB").get("change_pct"))
    tnx_pct = _safe_float(_get_series_item(fund, "^TNX").get("change_pct"))
    jgb10y_last = _safe_float(_get_series_item(fund, "JGB10Y=RR").get("last"))

    items: List[PolicyNewsItem] = []

    # 1) FX（円安/円高）
    if usd_jpy_pct is not None:
        if usd_jpy_pct >= THRESHOLDS["usd_jpy_pct"]:
            lib = EVENT_LIBRARY["yen_weak"]
            sd = {s: float(lib["sector_delta_each"]) for s in lib["sectors"]}
            items.append(
                _mk_item(
                    _id="evt_yen_weak",
                    title=f'{lib["title"]} (USDJPY {usd_jpy_pct:+.2f}%)',
                    category=lib["category"],
                    sectors=list(lib["sectors"]),
                    factors=dict(lib["factors"]),
                    sector_delta=sd,
                    reason="USDJPY change_pct が閾値を超過",
                    extra={"usd_jpy_pct": usd_jpy_pct},
                )
            )
        elif usd_jpy_pct <= -THRESHOLDS["usd_jpy_pct"]:
            lib = EVENT_LIBRARY["yen_strong"]
            sd = {s: float(lib["sector_delta_each"]) for s in lib["sectors"]}
            items.append(
                _mk_item(
                    _id="evt_yen_strong",
                    title=f'{lib["title"]} (USDJPY {usd_jpy_pct:+.2f}%)',
                    category=lib["category"],
                    sectors=list(lib["sectors"]),
                    factors=dict(lib["factors"]),
                    sector_delta=sd,
                    reason="USDJPY change_pct が閾値を超過",
                    extra={"usd_jpy_pct": usd_jpy_pct},
                )
            )

    # 2) RISK（DXYを簡易 proxy）
    if dxy_pct is not None:
        if dxy_pct >= THRESHOLDS["dxy_pct"]:
            lib = EVENT_LIBRARY["risk_off"]
            sd = {s: float(lib["sector_delta_each"]) for s in lib["sectors"]}
            items.append(
                _mk_item(
                    _id="evt_risk_off",
                    title=f'{lib["title"]} (DXY {dxy_pct:+.2f}%)',
                    category=lib["category"],
                    sectors=list(lib["sectors"]),
                    factors=dict(lib["factors"]),
                    sector_delta=sd,
                    reason="DXY change_pct が閾値を超過（簡易リスクオフ判定）",
                    extra={"dxy_pct": dxy_pct},
                )
            )
        elif dxy_pct <= -THRESHOLDS["dxy_pct"]:
            lib = EVENT_LIBRARY["risk_on"]
            sd = {s: float(lib["sector_delta_each"]) for s in lib["sectors"]}
            items.append(
                _mk_item(
                    _id="evt_risk_on",
                    title=f'{lib["title"]} (DXY {dxy_pct:+.2f}%)',
                    category=lib["category"],
                    sectors=list(lib["sectors"]),
                    factors=dict(lib["factors"]),
                    sector_delta=sd,
                    reason="DXY change_pct が閾値を超過（簡易リスクオン判定）",
                    extra={"dxy_pct": dxy_pct},
                )
            )

    # 3) RATES（^TNX / JGB10Y）
    rates_signal = 0
    if tnx_pct is not None:
        if tnx_pct >= THRESHOLDS["tnx_pct"]:
            rates_signal += 1
        elif tnx_pct <= -THRESHOLDS["tnx_pct"]:
            rates_signal -= 1

    if jgb10y_last is not None:
        if jgb10y_last >= THRESHOLDS["jgb10y_level_hi"]:
            rates_signal += 1
        elif jgb10y_last <= THRESHOLDS["jgb10y_level_lo"]:
            rates_signal -= 1

    if rates_signal >= 2:
        lib = EVENT_LIBRARY["rates_up"]
        sd = {s: float(lib["sector_delta_each"]) for s in lib["sectors"]}
        items.append(
            _mk_item(
                _id="evt_rates_up",
                title=f'{lib["title"]} (TNX={tnx_pct if tnx_pct is not None else "na"} / JGB10Y={jgb10y_last if jgb10y_last is not None else "na"})',
                category=lib["category"],
                sectors=list(lib["sectors"]),
                factors=dict(lib["factors"]),
                sector_delta=sd,
                reason="米金利変動と国内金利水準の合算シグナルが上向き",
                extra={"tnx_pct": tnx_pct, "jgb10y_last": jgb10y_last, "rates_signal": rates_signal},
            )
        )
    elif rates_signal <= -2:
        lib = EVENT_LIBRARY["rates_down"]
        sd = {s: float(lib["sector_delta_each"]) for s in lib["sectors"]}
        items.append(
            _mk_item(
                _id="evt_rates_down",
                title=f'{lib["title"]} (TNX={tnx_pct if tnx_pct is not None else "na"} / JGB10Y={jgb10y_last if jgb10y_last is not None else "na"})',
                category=lib["category"],
                sectors=list(lib["sectors"]),
                factors=dict(lib["factors"]),
                sector_delta=sd,
                reason="米金利変動と国内金利水準の合算シグナルが下向き",
                extra={"tnx_pct": tnx_pct, "jgb10y_last": jgb10y_last, "rates_signal": rates_signal},
            )
        )

    meta: Dict[str, Any] = {
        "engine": "policy_news_build",
        "schema": "policy_news_v1",
        "source": source,
        "built_at": datetime.now(JST).isoformat(),
        "fund_source": str(FUND_LATEST),
        "fund_asof": (fund.get("meta") or {}).get("asof") if isinstance(fund.get("meta"), dict) else None,
        "inputs": {
            "usd_jpy_pct": usd_jpy_pct,
            "dxy_pct": dxy_pct,
            "tnx_pct": tnx_pct,
            "jgb10y_last": jgb10y_last,
        },
        "thresholds": dict(THRESHOLDS),
    }

    snap = PolicyNewsSnapshot(asof=asof2, items=items, meta=meta)

    # 一度 dump→repoで再ロードして、repoの集計ロジックで factors_sum/sector_sum を確実に付ける
    tmp = dump_policy_news_snapshot(snap)
    s = json.dumps(tmp, ensure_ascii=False, separators=(",", ":"))
    _tmp_path = POLICY_NEWS_DIR / "__tmp_policy_news_build.json"
    _tmp_path.write_text(s, encoding="utf-8")
    snap2 = load_policy_news_snapshot(_tmp_path)
    try:
        _tmp_path.unlink(missing_ok=True)
    except Exception:
        pass

    # build側のmeta/asofを優先
    snap2.meta = meta
    snap2.asof = asof2
    return snap2


def emit_policy_news_json(snap: PolicyNewsSnapshot) -> None:
    POLICY_NEWS_DIR.mkdir(parents=True, exist_ok=True)

    payload = dump_policy_news_snapshot(snap)
    s = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

    # latest
    LATEST_POLICY_NEWS.write_text(s, encoding="utf-8")

    # stamped
    stamped = POLICY_NEWS_DIR / f"{dt_now_stamp()}_policy_news.json"
    stamped.write_text(s, encoding="utf-8")