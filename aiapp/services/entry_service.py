# -*- coding: utf-8 -*-
from __future__ import annotations
from typing import Dict, Any
import math

from .policy_loader import PolicyLoader

class EntryService:
    """
    Entry/TP/SLの提案（モード×ポリシー）
    暫定本番（短期×攻め）: entry=last、tp=last+1.5*ATR14、sl=last-1.0*ATR14
    数値はポリシーYAMLから取得。端数は整数丸め。
    """
    def __init__(self, loader: PolicyLoader | None = None):
        self.loader = loader or PolicyLoader()

    @staticmethod
    def _round_int(x: float) -> int:
        try:
            return int(round(float(x)))
        except Exception:
            return 0

    def propose(self, last: float, atr14: float, mode: str) -> Dict[str, int]:
        rule: Dict[str, Any] = self.loader.entry_rule(mode)
        # デフォルト係数
        k_entry = float(rule.get("entry_k", 0.0))  # 0 → last据置
        k_tp    = float(rule.get("tp_k", 1.5))
        k_sl    = float(rule.get("sl_k", 1.0))

        entry = last + k_entry * atr14
        tp    = last + k_tp    * atr14
        sl    = last - k_sl    * atr14

        return {
            "entry": self._round_int(entry),
            "tp":    self._round_int(tp),
            "sl":    self._round_int(sl),
        }