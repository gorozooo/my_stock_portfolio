# aiapp/services/fundamentals/repo.py
# -*- coding: utf-8 -*-
"""
財務ファンダスナップショットの読み書き（JSON）。

- picks_build_hybrid はここだけを読めばOK
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import date
from typing import Any, Dict, Optional

from aiapp.services.picks_build.utils import normalize_code
from .schema import FundamentalRow, FundamentalSnapshot
from .settings import FUND_DIR, FUND_LATEST


def load_fund_snapshot(path: Optional[str] = None) -> FundamentalSnapshot:
    p = FUND_LATEST if not path else FUND_DIR / path
    if not p.exists():
        return FundamentalSnapshot(asof=date.today().isoformat(), rows={}, meta={"source": "missing"})
    try:
        j = json.loads(p.read_text(encoding="utf-8"))
        asof = str(j.get("asof") or date.today().isoformat())
        meta = dict(j.get("meta") or {})
        rows_in = dict(j.get("rows") or {})
        rows: Dict[str, FundamentalRow] = {}
        for k, v in rows_in.items():
            code = normalize_code(k)
            if not code:
                continue
            rows[code] = FundamentalRow(
                code=code,
                asof=str(v.get("asof") or asof),
                fund_score=float(v.get("fund_score") or 0.0),
                flags=list(v.get("flags") or []),
                metrics=dict(v.get("metrics") or {}),
            )
        return FundamentalSnapshot(asof=asof, rows=rows, meta=meta)
    except Exception:
        return FundamentalSnapshot(asof=date.today().isoformat(), rows={}, meta={"source": "error"})


def save_fund_snapshot(snap: FundamentalSnapshot, *, stamp_name: Optional[str] = None) -> None:
    FUND_DIR.mkdir(parents=True, exist_ok=True)
    data = {
        "asof": snap.asof,
        "meta": snap.meta,
        "rows": {k: asdict(v) for k, v in snap.rows.items()},
    }
    FUND_LATEST.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    if stamp_name:
        (FUND_DIR / stamp_name).write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")