# aiapp/services/fundamentals/build_service.py
# -*- coding: utf-8 -*-
"""
fundamentals_build の中身（入力JSON → 正規化 → score付与 → latest保存）。

入力（input_fund.json）:
- rows[code].metrics に数値を詰めるだけで動く
- 取得元（API/スクレイピング/手入力）は後で差し替え可能
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

from .repo import save_fund_snapshot
from .schema import FundamentalRow, FundamentalSnapshot
from .scoring import score_fundamentals
from .settings import FUND_INPUT


def build_fundamentals_from_input() -> FundamentalSnapshot:
    if not FUND_INPUT.exists():
        snap = FundamentalSnapshot(asof="", rows={}, meta={"source": "missing_input"})
        save_fund_snapshot(snap)
        return snap

    try:
        j = json.loads(FUND_INPUT.read_text(encoding="utf-8"))
    except Exception:
        snap = FundamentalSnapshot(asof="", rows={}, meta={"source": "json_error"})
        save_fund_snapshot(snap)
        return snap

    asof = str(j.get("asof") or "")
    meta = dict(j.get("meta") or {})
    meta.setdefault("source", "input_fund.json")

    rows_in = dict(j.get("rows") or {})
    out: Dict[str, FundamentalRow] = {}

    for code, v in rows_in.items():
        c = str(code or "").strip()
        if not c:
            continue
        vv = dict(v or {})
        metrics = dict(vv.get("metrics") or {})

        fund_score, flags = score_fundamentals(metrics)

        out[c] = FundamentalRow(
            code=c,
            asof=str(vv.get("asof") or asof),
            fund_score=float(fund_score),
            flags=list(flags)[:10],
            metrics=metrics,
        )

    snap = FundamentalSnapshot(asof=asof, rows=out, meta=meta)
    save_fund_snapshot(snap)
    return snap