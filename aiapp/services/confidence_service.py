# -*- coding: utf-8 -*-
from __future__ import annotations
from typing import List, Dict, Any

from .policy_loader import PolicyLoader

class ConfidenceService:
    """
    AI信頼度（⭐1–5）
    - prod: 紙トレ指標ベース（未連携時はscore_100を代替）
    - dev : score_100の分位で割り当て
    """
    def __init__(self, loader: PolicyLoader | None = None):
        self.loader = loader or PolicyLoader()

    def stars_from_score100(self, score100: int, rule: Dict[str, Any]) -> int:
        # 例: bins = [20,40,60,80] に応じて1..5
        bins = rule.get("bins", [20, 40, 60, 80])
        if score100 < bins[0]:
            return 1
        if score100 < bins[1]:
            return 2
        if score100 < bins[2]:
            return 3
        if score100 < bins[3]:
            return 4
        return 5

    def batch_assign(self, score100_list: List[int]) -> List[int]:
        profile = self.loader.get_profile()
        rule = self.loader.confidence_rule(profile)
        # 将来: prod の紙トレ指標が連携されたらここで参照
        return [self.stars_from_score100(s, rule) for s in score100_list]