# aiapp/services/policy_build/repo.py
# -*- coding: utf-8 -*-

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict

POLICY_LATEST = Path("media/aiapp/policy/latest_policy.json")


@dataclass
class PolicySnapshot:
    asof: str
    sector_rows: Dict[str, Any]
    meta: Dict[str, Any]


def load_policy_snapshot() -> PolicySnapshot:
    if not POLICY_LATEST.exists():
        return PolicySnapshot(asof="1970-01-01", sector_rows={}, meta={"error": "missing", "source": str(POLICY_LATEST)})

    try:
        j = json.loads(POLICY_LATEST.read_text(encoding="utf-8"))
    except Exception:
        return PolicySnapshot(asof="1970-01-01", sector_rows={}, meta={"error": "invalid_json", "source": str(POLICY_LATEST)})

    asof = str(j.get("asof") or "1970-01-01")
    sector_rows = j.get("sector_rows") if isinstance(j.get("sector_rows"), dict) else {}
    meta = j.get("meta") if isinstance(j.get("meta"), dict) else {}
    return PolicySnapshot(asof=asof, sector_rows=sector_rows, meta=meta)