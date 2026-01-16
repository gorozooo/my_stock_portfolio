# aiapp/services/policy_news/schema.py
# -*- coding: utf-8 -*-
"""
これは何のファイル？
- policy_news の出力スキーマ（JSONの形をPythonで固定する）。

設計意図:
- ニュース/政策/社会情勢を「fx / rates / risk」の因子に落とし、
  さらにセクター別に delta を持てるようにする。
- policy_build 側はこの snapshot を読むだけで合流できる。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class PolicyNewsItem:
    """
    1ニュース（1要因）の単位。
    - impact: fx/rates/risk の因子（-10..+10 くらいの“小さめ”を想定）
    - sector_delta: セクター別に個別上乗せ（無ければimpactのみで共通適用でもOK）
    """
    id: str
    category: str = "misc"
    title: Optional[str] = None

    impact: Dict[str, float] = field(default_factory=dict)        # {"fx": +0.5, "rates": -0.2, "risk": +0.3}
    sector_delta: Dict[str, float] = field(default_factory=dict)  # {"銀行業": +1.2, "不動産業": -1.0}

    reason: Optional[str] = None
    source: Optional[str] = None
    url: Optional[str] = None


@dataclass
class PolicyNewsSnapshot:
    """
    その日の policy_news 全体。
    - asof: YYYY-MM-DD（policy_build と揃える）
    - items: PolicyNewsItem の配列
    - meta: 生成エンジン情報など
    """
    asof: str
    items: List[PolicyNewsItem] = field(default_factory=list)
    meta: Dict[str, object] = field(default_factory=dict)

    # 集計（repo/build_service側で埋めてもOK）
    factors_sum: Dict[str, float] = field(default_factory=dict)   # {"fx":..., "rates":..., "risk":...}
    sector_sum: Dict[str, float] = field(default_factory=dict)    # {"銀行業":..., ...}