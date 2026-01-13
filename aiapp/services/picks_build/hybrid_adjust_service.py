# aiapp/services/picks_build/hybrid_adjust_service.py
# -*- coding: utf-8 -*-
"""
B側（テクニカル×ファンダ×政策）の “合成” を行うサービス。

方針（強め＆壊さない）:
- テクニカルの EV_true_rakuten を “ベース” として尊重
- そこに「ファンダ(0..100)」「政策(-20..+20)」を小さめに混ぜる
- 混ぜた結果を ev_true_rakuten_hybrid に置く（元は保持）
- ランキングは hybrid を優先して並び替える（B専用コマンド側）

混ぜ方（初期）:
- fund_bonus = (fund_score - 50) * 0.04   → だいたい -2 .. +2
- policy_bonus = policy_score * 0.20      → だいたい -4 .. +4
- total_bonus = clamp(fund_bonus + policy_bonus, -6, +6)

つまり “テクニカルが強い銘柄を壊さずに、上げ下げの微調整” になる。
"""

from __future__ import annotations

from typing import Dict, List, Tuple

from aiapp.services.fundamentals.repo import load_fund_snapshot
from aiapp.services.policy_news.repo import load_policy_snapshot

from .schema import PickItem


def _clamp(v: float, lo: float, hi: float) -> float:
    if v < lo:
        return lo
    if v > hi:
        return hi
    return v


def apply_hybrid_adjust(items: List[PickItem]) -> Dict[str, int]:
    """
    items を in-place で拡張する（B専用）。
    戻り値は簡易stats（何件に適用できたかなど）。
    """
    stats = {
        "fund_hit": 0,
        "policy_hit": 0,
        "both_hit": 0,
        "none_hit": 0,
    }

    fund_snap = load_fund_snapshot()
    pol_snap = load_policy_snapshot()

    # sector→policy row
    policy_map = pol_snap.sector_rows or {}
    fund_map = fund_snap.rows or {}

    for it in items:
        code = str(it.code or "").strip()
        sec = str(it.sector_display or "").strip()

        fund_score = None
        fund_flags = None
        if code and code in fund_map:
            fr = fund_map[code]
            fund_score = float(fr.fund_score)
            fund_flags = list(fr.flags or [])[:10]
            stats["fund_hit"] += 1

        policy_score = None
        policy_flags = None
        if sec and sec in policy_map:
            pr = policy_map[sec]
            policy_score = float(pr.policy_score)
            policy_flags = list(pr.flags or [])[:10]
            stats["policy_hit"] += 1

        if fund_score is not None and policy_score is not None:
            stats["both_hit"] += 1
        elif fund_score is None and policy_score is None:
            stats["none_hit"] += 1

        # --- bonus計算 ---
        fund_bonus = 0.0
        if fund_score is not None:
            fund_bonus = (fund_score - 50.0) * 0.04  # -2..+2 くらい

        pol_bonus = 0.0
        if policy_score is not None:
            pol_bonus = policy_score * 0.20  # -4..+4 くらい

        total_bonus = _clamp(fund_bonus + pol_bonus, -6.0, 6.0)

        # --- 書き込み（後方互換: 新キーだけ増やす） ---
        it.fund_score = fund_score
        it.fund_flags = fund_flags
        it.policy_score = policy_score
        it.policy_flags = policy_flags
        it.hybrid_bonus = float(total_bonus) if (fund_score is not None or policy_score is not None) else None

        base_ev = it.ev_true_rakuten
        if base_ev is None:
            it.ev_true_rakuten_hybrid = None
        else:
            try:
                it.ev_true_rakuten_hybrid = float(base_ev) + float(total_bonus)
            except Exception:
                it.ev_true_rakuten_hybrid = None

    return stats