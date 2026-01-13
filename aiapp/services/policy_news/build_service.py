# aiapp/services/policy_news/build_service.py
# -*- coding: utf-8 -*-
"""
policy_build の中身（入力JSON → 正規化 → latestに保存）。

ポイント:
- この段階では “業種別 policy_score/flags をそのまま採用” が目的
- つまり「政治・政策をどう数値化するか」は input 側で自由にやれて、
  build 側は落ちない＆キー揺れを整えるだけ
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Tuple

from .repo import save_policy_snapshot
from .schema import PolicySectorRow, PolicySnapshot
from .settings import POLICY_INPUT


def _as_float(x) -> float:
    try:
        if x is None:
            return 0.0
        return float(x)
    except Exception:
        return 0.0


def _as_list(x) -> List[str]:
    if x is None:
        return []
    if isinstance(x, list):
        return [str(v) for v in x if v is not None][:10]
    return [str(x)]


def build_policy_from_input() -> PolicySnapshot:
    if not POLICY_INPUT.exists():
        snap = PolicySnapshot(asof="", sector_rows={}, meta={"source": "missing_input"})
        save_policy_snapshot(snap)
        return snap

    try:
        j = json.loads(POLICY_INPUT.read_text(encoding="utf-8"))
    except Exception:
        snap = PolicySnapshot(asof="", sector_rows={}, meta={"source": "json_error"})
        save_policy_snapshot(snap)
        return snap

    asof = str(j.get("asof") or "")
    meta = dict(j.get("meta") or {})
    meta.setdefault("source", "input_policy.json")

    rows_in = dict(j.get("sector_rows") or {})
    out_rows: Dict[str, PolicySectorRow] = {}

    for k, v in rows_in.items():
        sector = str(k or "").strip()
        if not sector:
            continue

        vv = dict(v or {})
        score = _as_float(vv.get("policy_score"))
        flags = _as_list(vv.get("flags"))
        m = dict(vv.get("meta") or {})

        out_rows[sector] = PolicySectorRow(
            sector_display=sector,
            policy_score=score,
            flags=flags,
            meta=m,
        )

    snap = PolicySnapshot(asof=asof, sector_rows=out_rows, meta=meta)
    save_policy_snapshot(snap)
    return snap