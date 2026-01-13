# aiapp/services/policy_news/repo.py
# -*- coding: utf-8 -*-
"""
政策・社会情勢スナップショット（JSON）の読み書き。
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import date
from typing import Dict, Optional

from .schema import PolicySectorRow, PolicySnapshot
from .settings import POLICY_DIR, POLICY_LATEST


def load_policy_snapshot(path: Optional[str] = None) -> PolicySnapshot:
    p = POLICY_LATEST if not path else POLICY_DIR / path
    if not p.exists():
        return PolicySnapshot(asof=date.today().isoformat(), sector_rows={}, meta={"source": "missing"})
    try:
        j = json.loads(p.read_text(encoding="utf-8"))
        asof = str(j.get("asof") or date.today().isoformat())
        meta = dict(j.get("meta") or {})
        rows_in = dict(j.get("sector_rows") or {})
        rows: Dict[str, PolicySectorRow] = {}
        for k, v in rows_in.items():
            sec = str(k or "").strip()
            if not sec:
                continue
            rows[sec] = PolicySectorRow(
                sector_display=sec,
                policy_score=float(v.get("policy_score") or 0.0),
                flags=list(v.get("flags") or []),
                meta=dict(v.get("meta") or {}),
            )
        return PolicySnapshot(asof=asof, sector_rows=rows, meta=meta)
    except Exception:
        return PolicySnapshot(asof=date.today().isoformat(), sector_rows={}, meta={"source": "error"})


def save_policy_snapshot(snap: PolicySnapshot, *, stamp_name: Optional[str] = None) -> None:
    POLICY_DIR.mkdir(parents=True, exist_ok=True)
    data = {
        "asof": snap.asof,
        "meta": snap.meta,
        "sector_rows": {k: asdict(v) for k, v in snap.sector_rows.items()},
    }
    POLICY_LATEST.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    if stamp_name:
        (POLICY_DIR / stamp_name).write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")