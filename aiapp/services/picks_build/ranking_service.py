# aiapp/services/picks_build/ranking_service.py
# -*- coding: utf-8 -*-
"""
ランキング（並び替え）と TopK 抽出。

- 本番キー:
    EV_true_rakuten desc → qty_rakuten>0 → confirm_score → ml_rank → score_100 → last_close
  ※ EV_true 主キーは絶対に維持。confirm は “同EV帯の中で” 押し上げる役。

- TopK: 原則 EV_true>0 & qty>0
"""

from __future__ import annotations

from typing import List, Tuple

from .schema import PickItem
from .utils import as_float_or_none


def sort_items_inplace(items: List[PickItem]) -> None:
    def _rank_key(x: PickItem):
        ev_true = as_float_or_none(x.ev_true_rakuten)
        ev_key = ev_true if ev_true is not None else -1e18

        qty = int(x.qty_rakuten or 0)
        qty_ok = 1 if qty > 0 else 0

        # ★追加：confirm_score（無ければ 0 扱い）
        conf = int(x.confirm_score or 0)
        conf_key = conf

        mr = as_float_or_none(x.ml_rank)
        mr_key = mr if mr is not None else -1e18

        sc = float(x.score_100) if x.score_100 is not None else -1e18
        lc = float(x.last_close) if x.last_close is not None else -1e18

        return (ev_key, qty_ok, conf_key, mr_key, sc, lc)

    items.sort(key=_rank_key, reverse=True)


def select_topk(items: List[PickItem], topk: int) -> Tuple[List[PickItem], str]:
    top_candidates: List[PickItem] = []
    for it in items:
        ev_true = as_float_or_none(it.ev_true_rakuten)
        qty = int(it.qty_rakuten or 0)
        if ev_true is not None and ev_true > 0 and qty > 0:
            top_candidates.append(it)

    top_items = top_candidates[: max(0, topk)]
    if not top_items:
        top_items = items[: max(0, topk)]
        return top_items, "fallback:sorted_top"
    return top_items, "rule:ev_true>0_and_qty>0"