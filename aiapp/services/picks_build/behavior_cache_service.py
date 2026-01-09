# -*- coding: utf-8 -*-
"""
BehaviorStats の一括ロードを担当。

- DB連打を防ぐため、(code, mode_period, mode_aggr) -> stats dict のキャッシュを作る
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

# オプション扱い（無くても動く）
try:
    from aiapp.models.behavior_stats import BehaviorStats
except Exception:  # pragma: no cover
    BehaviorStats = None  # type: ignore

from .utils import normalize_code


def load_behavior_cache(
    codes: List[str],
) -> Dict[Tuple[str, str, str], Dict[str, Any]]:
    behavior_cache: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    if BehaviorStats is None or not codes:
        return behavior_cache

    try:
        codes_norm = [normalize_code(c) for c in codes if c]
        qs = (
            BehaviorStats.objects
            .filter(code__in=codes_norm)
            .values("code", "mode_period", "mode_aggr", "stars", "n", "win_rate", "avg_pl")
        )
        for r in qs:
            c = normalize_code(r.get("code"))
            mp = (r.get("mode_period") or "").strip().lower()
            ma = (r.get("mode_aggr") or "").strip().lower()
            if not c or not mp or not ma:
                continue
            behavior_cache[(c, mp, ma)] = {
                "stars": r.get("stars"),
                "n": r.get("n"),
                "win_rate": r.get("win_rate"),
                "avg_pl": r.get("avg_pl"),
            }
        return behavior_cache
    except Exception:
        return {}