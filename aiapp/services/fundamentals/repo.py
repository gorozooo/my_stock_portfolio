# aiapp/services/fundamentals/repo.py
# -*- coding: utf-8 -*-
"""
財務ファンダスナップショットの保存/読み込み。

- 書き込み: latest_fund.json（必須） + timestampファイル（任意）
- 読み込み: latest_fund.json（無ければ空で返す）
"""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Dict, Optional

from .schema import FundamentalRow, FundamentalSnapshot
from .settings import FUND_DIR, FUND_LATEST


def save_fund_snapshot(snap: FundamentalSnapshot, *, stamp_name: Optional[str] = None) -> None:
    FUND_DIR.mkdir(parents=True, exist_ok=True)

    payload = asdict(snap)
    FUND_LATEST.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    if stamp_name:
        p = FUND_DIR / stamp_name
        p.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def load_fund_snapshot() -> FundamentalSnapshot:
    if not FUND_LATEST.exists():
        return FundamentalSnapshot(asof="", rows={}, meta={"source": "missing_latest"})

    try:
        j = json.loads(FUND_LATEST.read_text(encoding="utf-8"))
    except Exception:
        return FundamentalSnapshot(asof="", rows={}, meta={"source": "json_error"})

    asof = str(j.get("asof") or "")
    meta = dict(j.get("meta") or {})

    rows_in = dict(j.get("rows") or {})
    rows: Dict[str, FundamentalRow] = {}

    for code, v in rows_in.items():
        c = str(code or "").strip()
        if not c:
            continue
        vv = dict(v or {})
        row_asof = str(vv.get("asof") or asof)
        fund_score = float(vv.get("fund_score") or 0.0)
        flags = list(vv.get("flags") or [])[:10]
        metrics = dict(vv.get("metrics") or {})
        rows[c] = FundamentalRow(code=c, asof=row_asof, fund_score=fund_score, flags=flags, metrics=metrics)

    return FundamentalSnapshot(asof=asof, rows=rows, meta=meta)