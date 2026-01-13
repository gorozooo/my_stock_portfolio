# aiapp/services/policy_news/schema.py
# -*- coding: utf-8 -*-
"""
政策・社会情勢スナップショットのスキーマ。

方針:
- 政治/政策/地政学/社会イベントは “追い風/逆風/不確実性” として定量化
- 個別銘柄に直接当てるより、まずは sector_display を介して当てる（安定する）
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List


@dataclass
class PolicySectorRow:
    sector_display: str
    policy_score: float          # -30..+30 目安
    flags: List[str]             # 短い理由（最大10）
    meta: Dict[str, Any]         # 参照ID/日付/見出し等を入れて監査可能に


@dataclass
class PolicySnapshot:
    asof: str
    sector_rows: Dict[str, PolicySectorRow]  # key=sector_display
    meta: Dict[str, Any]