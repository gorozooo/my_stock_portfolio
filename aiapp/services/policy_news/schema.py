# aiapp/services/policy_news/schema.py
# -*- coding: utf-8 -*-
"""
政策・社会情勢スナップショットのスキーマ。

- policy_build が input_policy.json を正規化して latest_policy.json に落とす
- picks_build_hybrid が sector_display をキーに参照して “政策スコア/フラグ” を加点減点に使う想定
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List


@dataclass
class PolicySectorRow:
    sector_display: str
    policy_score: float  # 例: -20 .. +20（運用しながら調整）
    flags: List[str]
    meta: Dict[str, Any]


@dataclass
class PolicySnapshot:
    asof: str
    sector_rows: Dict[str, PolicySectorRow]  # key = sector_display
    meta: Dict[str, Any]