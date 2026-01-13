# aiapp/services/picks_build_hybrid/hybrid_boost_service.py
# -*- coding: utf-8 -*-
"""
Hybrid(B) 用：ファンダ（財務）+ 政策/社会情勢を EV に合成する。

方針:
- 主軸は EV_true_rakuten（Aの思想を壊さない）
- ファンダ/政策は “上げる” より “地雷回避（減点）” を強くする
"""

from __future__ import annotations

from typing import List, Optional, Tuple

from aiapp.services.picks_build.utils import as_float_or_none


def compute_hybrid_boost(
    *,
    ev_true_rakuten: Optional[float],
    fund_score: Optional[float],
    policy_score: Optional[float],
    fund_flags: Optional[List[str]] = None,
    policy_flags: Optional[List[str]] = None,
) -> Tuple[float, float]:
    """
    returns: (hybrid_boost, hybrid_score)

    初期パラメータ（安全寄り）:
    - fund: 0..100 を 50基準の偏差で効かせる（α）
    - policy: -30..+30 をそのまま小さく効かせる（β）
    - ev_true が None の場合は 0 として扱う（落ちない）
    """
    ev = as_float_or_none(ev_true_rakuten) or 0.0

    # 重み（最初は控えめ）
    alpha = 0.15  # fund
    beta = 0.10   # policy

    boost = 0.0

    # fund_score: 50が中立
    if fund_score is not None:
        dev = float(fund_score) - 50.0
        boost += alpha * dev

        # 地雷回避の追加減点（flagsに依存）
        if fund_flags:
            if any("財務弱い" in x for x in fund_flags):
                boost -= 6.0
            if any("減収" in x for x in fund_flags):
                boost -= 4.0

    # policy_score: -30..+30
    if policy_score is not None:
        boost += beta * float(policy_score)

        # 不確実性（イベント/規制）系は減点を少し厚く
        if policy_flags:
            if any("規制逆風" in x for x in policy_flags):
                boost -= 5.0
            if any("地政学" in x for x in policy_flags):
                boost -= 4.0

    hybrid_score = ev + boost
    return float(boost), float(hybrid_score)