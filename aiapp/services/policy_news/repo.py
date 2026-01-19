# aiapp/services/policy_news/repo.py
# -*- coding: utf-8 -*-
"""
policy_news の JSON を読む層（repo）。

方針:
- 読めなくても落とさない
- items を PolicyNewsItem に復元
- factors_sum / sector_sum を集計して返す

注意:
- PolicyNewsItem のスキーマは今後揺れる可能性があるため、
  __init__ が受け取れるキーだけを自動選別して渡す。
"""

from __future__ import annotations

import inspect
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


def _make_policy_news_item(**kwargs) -> PolicyNewsItem:
    sig = inspect.signature(PolicyNewsItem)
    allowed = set(sig.parameters.keys())
    filtered = {k: v for k, v in kwargs.items() if k in allowed}
    return PolicyNewsItem(**filtered)


def _parse_item(d: Dict[str, Any]) -> Optional[PolicyNewsItem]:
    if not isinstance(d, dict):
        return None

    _id = _norm_text(d.get("id"))
    if not _id:
        return None

    title = _norm_text(d.get("title")) or None
    category = _norm_text(d.get("category")) or "misc"
    reason = _norm_text(d.get("reason")) or None
    src = _norm_text(d.get("source")) or None
    url = _norm_text(d.get("url")) or None

    # sectors（あれば）
    sectors_in = _safe_list(d.get("sectors"))
    sectors = [str(x).strip() for x in sectors_in if str(x).strip()]

    # factors は factors / impact どちらでも拾う（互換）
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

    return _make_policy_news_item(
        id=_id,
        category=category,
        title=title,
        sectors=sectors,
        factors=factors,
        sector_delta=sector_delta,
        reason=reason,
        source=src,
        url=url,
    )


def load_policy_news_snapshot(path: Optional[Path] = None) -> PolicyNewsSnapshot:
    """
    latest_policy_news.json を読み、PolicyNewsSnapshot を返す。
    読めない場合でも空を返して落とさない。
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
        # factors / impact どちらのフィールド名でも対応できるように getattr で拾う
        fx_dict = getattr(it, "factors", None)
        if not isinstance(fx_dict, dict):
            fx_dict = getattr(it, "impact", None)
        if not isinstance(fx_dict, dict):
            fx_dict = {}

        for k, v in fx_dict.items():
            if k in factors_sum and v is not None:
                try:
                    factors_sum[k] += float(v)
                except Exception:
                    pass

        sd = getattr(it, "sector_delta", None)
        if isinstance(sd, dict):
            for sec, dv in sd.items():
                try:
                    k = _norm_text(sec)
                    if not k:
                        continue
                    sector_sum[k] = float(sector_sum.get(k, 0.0)) + float(dv)
                except Exception:
                    pass

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