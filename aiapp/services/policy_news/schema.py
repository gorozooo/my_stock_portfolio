# aiapp/services/policy_news/schema.py
# -*- coding: utf-8 -*-
"""
policy_news のスキーマ（dataclass）

方針:
- 人間が毎日更新するseedは廃止（入力ファイルに依存しない）
- policy_news_build が市場データから「イベント」を自動生成する
- repo 層で factors_sum / sector_sum を集計できる形にする
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class PolicyNewsItem:
    """
    1イベント（ニュース/政策/社会情勢…と言いつつ、当面は市場イベント）
    - sectors: 影響対象の33業種（表示名）
    - factors: {"fx":..., "rates":..., "risk":...}  ※ policy_build 側の news_factors_sum に流れる
    - sector_delta: {"輸送用機器": +0.12, ...}      ※ policy_build 側の news_sector_sum（base）に流れる
    """
    id: str
    title: Optional[str] = None
    category: str = "market"
    sectors: Optional[List[str]] = None
    factors: Optional[Dict[str, float]] = None
    sector_delta: Optional[Dict[str, float]] = None
    reason: Optional[str] = None
    source: Optional[str] = None
    url: Optional[str] = None
    extra: Optional[Dict[str, Any]] = None


@dataclass
class PolicyNewsSnapshot:
    asof: str
    items: List[PolicyNewsItem] = field(default_factory=list)
    meta: Dict[str, Any] = field(default_factory=dict)
    factors_sum: Dict[str, float] = field(default_factory=dict)  # {"fx":..., "rates":..., "risk":...}
    sector_sum: Dict[str, float] = field(default_factory=dict)   # {"輸送用機器":..., ...}