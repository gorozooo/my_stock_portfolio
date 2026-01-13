# aiapp/services/fundamentals/schema.py
# -*- coding: utf-8 -*-
"""
財務ファンダスナップショットのスキーマ。

- fundamentals_build が input_fund.json を正規化し、fund_score/flags を付けて latest_fund.json に保存
- picks_build_hybrid が code をキーに参照して “財務スコア/フラグ” を加点減点に使う想定
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List


@dataclass
class FundamentalRow:
    code: str
    asof: str
    fund_score: float  # 0..100（scoringで算出）
    flags: List[str]
    metrics: Dict[str, Any]


@dataclass
class FundamentalSnapshot:
    asof: str
    rows: Dict[str, FundamentalRow]  # key = code
    meta: Dict[str, Any]