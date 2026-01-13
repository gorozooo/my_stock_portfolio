# aiapp/services/policy_news/repo.py
# -*- coding: utf-8 -*-
"""
政策スナップショットの保存/読み込み。

- 書き込み: latest_policy.json（必須） + timestampファイル（任意）
- 読み込み: latest_policy.json（無ければ空で返す）
"""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any, Dict, Optional

from .schema import PolicySectorRow, PolicySnapshot
from .settings import POLICY_DIR, POLICY_LATEST


def save_policy_snapshot(snap: PolicySnapshot, *, stamp_name: Optional[str] = None) -> None:
    POLICY_DIR.mkdir(parents=True, exist_ok=True)

    payload = asdict(snap)
    POLICY_LATEST.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    if stamp_name:
        p = POLICY_DIR / stamp_name
        p.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def load_policy_snapshot() -> PolicySnapshot:
    if not POLICY_LATEST.exists():
        return PolicySnapshot(asof="", sector_rows={}, meta={"source": "missing_latest"})

    try:
        j = json.loads(POLICY_LATEST.read_text(encoding="utf-8"))
    except Exception:
        return PolicySnapshot(asof="", sector_rows={}, meta={"source": "json_error"})

    asof = str(j.get("asof") or "")
    meta = dict(j.get("meta") or {})

    rows_in = dict(j.get("sector_rows") or {})
    rows: Dict[str, PolicySectorRow] = {}

    for sec, v in rows_in.items():
        sector = str(sec or "").strip()
        if not sector:
            continue
        vv = dict(v or {})
        policy_score = float(vv.get("policy_score") or 0.0)
        flags = list(vv.get("flags") or [])[:10]
        m = dict(vv.get("meta") or {})
        rows[sector] = PolicySectorRow(sector_display=sector, policy_score=policy_score, flags=flags, meta=m)

    return PolicySnapshot(asof=asof, sector_rows=rows, meta=meta)