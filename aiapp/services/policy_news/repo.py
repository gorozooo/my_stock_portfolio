# aiapp/services/policy_news/repo.py
# -*- coding: utf-8 -*-
"""
これは何のファイル？
- policy_news の JSON を「読む」だけの層（repo）。

方針:
- 読めなくても落とさない（欠損でもhybrid全体が止まらないため）
- 読めたら schema に沿って PolicyNewsSnapshot を返す
- factors_sum / sector_sum は repo 側で軽く集計して返す
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, Optional, List

from .schema import PolicyNewsItem, PolicyNewsSnapshot
from .settings import LATEST_POLICY_NEWS


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


def _safe_dict(v) -> Dict[str, Any]:
    return v if isinstance(v, dict) else {}


def _safe_list(v) -> List[Any]:
    return v if isinstance(v, list) else []


def _norm_text(s: Any) -> str:
    return str(s or "").strip()


def _parse_item(d: Dict[str, Any]) -> Optional[PolicyNewsItem]:
    if not isinstance(d, dict):
        return None

    _id = _norm_text(d.get("id"))
    if not _id:
        return None

    # factors は factors / impact のどちらでも受ける（互換）
    factors_in = _safe_dict(d.get("factors")) or _safe_dict(d.get("impact"))
    factors: Dict[str, float] = {}
    for k in ("fx", "rates", "risk"):
        fv = _safe_float(factors_in.get(k))
        if fv is not None:
            factors[k] = float(fv)

    # sector_delta
    sector_in = _safe_dict(d.get("sector_delta"))
    sector_delta: Dict[str, float] = {}
    for k, v in sector_in.items():
        kk = _norm_text(k)
        fv = _safe_float(v)
        if kk and fv is not None:
            sector_delta[kk] = float(fv)

    # meta（任意）
    meta = _safe_dict(d.get("meta"))

    return PolicyNewsItem(
        id=_id,
        category=_norm_text(d.get("category")) or "misc",
        title=_norm_text(d.get("title")) or None,
        impact=factors,          # schema上は impact として保持（中身は factors）
        sector_delta=sector_delta,
        reason=_norm_text(d.get("reason")) or None,
        source=_norm_text(d.get("source")) or None,
        url=_norm_text(d.get("url")) or None,
        meta=meta,
    )


def load_policy_news_snapshot(path: Optional[Path] = None) -> PolicyNewsSnapshot:
    """
    latest_policy_news.json を読み、PolicyNewsSnapshot を返す。
    読めない場合でも「空のsnapshot」を返して落とさない。
    """
    p = path or LATEST_POLICY_NEWS

    if not p.exists():
        return PolicyNewsSnapshot(
            asof="1970-01-01",
            items=[],
            meta={"error": "missing", "source": str(p)},
            factors_sum={},
            sector_sum={},
        )

    try:
        j = json.loads(p.read_text(encoding="utf-8"))
    except Exception as ex:
        return PolicyNewsSnapshot(
            asof="1970-01-01",
            items=[],
            meta={"error": f"invalid_json:{ex}", "source": str(p)},
            factors_sum={},
            sector_sum={},
        )

    asof = _norm_text(j.get("asof")) or "1970-01-01"
    meta = _safe_dict(j.get("meta"))

    items: List[PolicyNewsItem] = []
    for raw in _safe_list(j.get("items")):
        it = _parse_item(raw if isinstance(raw, dict) else {})
        if it is not None:
            items.append(it)

    # 集計
    factors_sum: Dict[str, float] = {"fx": 0.0, "rates": 0.0, "risk": 0.0}
    sector_sum: Dict[str, float] = {}

    for it in items:
        for k, v in (it.impact or {}).items():
            if k in factors_sum and v is not None:
                factors_sum[k] += float(v)

        for sec, dv in (it.sector_delta or {}).items():
            sector_sum[sec] = float(sector_sum.get(sec, 0.0)) + float(dv)

    return PolicyNewsSnapshot(
        asof=asof,
        items=items,
        meta=meta,
        factors_sum=factors_sum,
        sector_sum=sector_sum,
    )


def dump_policy_news_snapshot(snap: PolicyNewsSnapshot) -> Dict[str, Any]:
    """
    dataclass → JSON dict（emit用）。
    """
    return {
        "asof": snap.asof,
        "items": [asdict(x) for x in (snap.items or [])],
        "meta": snap.meta or {},
        "factors_sum": snap.factors_sum or {},
        "sector_sum": snap.sector_sum or {},
    }