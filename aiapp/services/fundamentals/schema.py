# aiapp/services/fundamentals/schema.py
# -*- coding: utf-8 -*-
"""
財務ファンダスナップショットのスキーマ。

- code: "7203" のような正規化済みコードを想定
- fund_score: 0..100
- flags: UI表示用の短い理由（最大10程度）
- metrics: 生値（後で拡張しやすいように dict で保持）
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass
class FundamentalRow:
    code: str
    asof: str  # "YYYY-MM-DD"
    fund_score: float
    flags: List[str]
    metrics: Dict[str, Any]


@dataclass
class FundamentalSnapshot:
    asof: str
    rows: Dict[str, FundamentalRow]  # key=code
    meta: Dict[str, Any]