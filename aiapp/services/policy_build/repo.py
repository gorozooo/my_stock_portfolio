# aiapp/services/policy_build/repo.py
# -*- coding: utf-8 -*-

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

POLICY_LATEST = Path("media/aiapp/policy/latest_policy.json")


@dataclass
class PolicyRow:
    sector_display: str
    policy_score: Optional[float]
    flags: List[str]
    meta: Dict[str, Any]


@dataclass
class PolicySnapshot:
    asof: str
    sector_rows: Dict[str, PolicyRow]
    meta: Dict[str, Any]


def _safe_float(v: Any) -> Optional[float]:
    try:
        if v is None:
            return None
        x = float(v)
        if x != x:  # NaN
            return None
        return x
    except Exception:
        return None


def load_policy_snapshot() -> PolicySnapshot:
    if not POLICY_LATEST.exists():
        return PolicySnapshot(
            asof="1970-01-01",
            sector_rows={},
            meta={"error": "missing", "source": str(POLICY_LATEST)},
        )

    try:
        j = json.loads(POLICY_LATEST.read_text(encoding="utf-8"))
    except Exception:
        return PolicySnapshot(
            asof="1970-01-01",
            sector_rows={},
            meta={"error": "invalid_json", "source": str(POLICY_LATEST)},
        )

    asof = str(j.get("asof") or "1970-01-01")
    meta = j.get("meta") if isinstance(j.get("meta"), dict) else {}

    raw_rows = j.get("sector_rows") if isinstance(j.get("sector_rows"), dict) else {}
    out: Dict[str, PolicyRow] = {}

    for k, v in raw_rows.items():
        if not isinstance(v, dict):
            continue

        sector_display = str(v.get("sector_display") or k or "")
        policy_score = _safe_float(v.get("policy_score"))

        flags: List[str] = []
        fs = v.get("flags")
        if isinstance(fs, list):
            flags = [str(x) for x in fs if str(x).strip()]

        m = v.get("meta") if isinstance(v.get("meta"), dict) else {}

        out[str(k)] = PolicyRow(
            sector_display=sector_display,
            policy_score=policy_score,
            flags=flags,
            meta=m,
        )

    return PolicySnapshot(asof=asof, sector_rows=out, meta=meta)