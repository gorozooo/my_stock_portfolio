# -*- coding: utf-8 -*-
"""
aiapp.services.picks_bias

・セクタートレンドを少しだけスコアに反映
・大型株をやや優遇、小型株をやや減点

を行う「後段バイアス」レイヤー。
picks_build で PickItem のリストが揃った後に apply_all(items) を呼ぶ想定。
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, Iterable, List, Optional

import numpy as np

try:
    from aiapp.models import StockMaster
except Exception:  # pragma: no cover
    StockMaster = None  # type: ignore

try:
    from aiapp.services.scoring_service import stars_from_score
except Exception:  # pragma: no cover
    stars_from_score = None  # type: ignore


def _clamp01(x: float) -> float:
    if not np.isfinite(x):
        return 0.0
    return float(min(1.0, max(0.0, x)))


def _load_meta_for_items(items: Iterable[Any]) -> Dict[str, Dict[str, Any]]:
    """
    StockMaster から code ごとの market_cap / sector_name を取得。
    無ければ空 dict を返す。
    """
    if StockMaster is None:
        return {}
    codes = sorted({str(getattr(it, "code", "")).strip() for it in items if getattr(it, "code", None)})
    if not codes:
        return {}
    try:
        qs = StockMaster.objects.filter(code__in=codes).values("code", "market_cap", "sector_name")
        meta: Dict[str, Dict[str, Any]] = {}
        for r in qs:
            code = str(r.get("code"))
            meta[code] = {
                "market_cap": r.get("market_cap"),
                "sector_name": r.get("sector_name"),
            }
        return meta
    except Exception:
        return {}


def _size_bias(market_cap: Optional[float]) -> float:
    """
    時価総額に応じてごく小さなバイアスを返す。
    - 超大型（~1兆円以上）: +0.03
    - 大型（~3,000億〜1兆円）: +0.015
    - 小型（~300億未満）: -0.02
    - それ以外: 0
    """
    if market_cap is None:
        return 0.0
    try:
        mc = float(market_cap)
    except Exception:
        return 0.0
    if not np.isfinite(mc) or mc <= 0:
        return 0.0

    if mc >= 1e12:
        return 0.03
    if mc >= 3e11:
        return 0.015
    if mc <= 3e10:
        return -0.02
    return 0.0


def _compute_sector_strength(items: Iterable[Any]) -> Dict[str, float]:
    """
    items の生スコアから「セクターごとの平均スコア」をざっくり計算。
    """
    buckets: Dict[str, List[float]] = defaultdict(list)
    for it in items:
        sec = getattr(it, "sector_display", None) or "UNKNOWN"
        s = getattr(it, "score", None)
        try:
            s = float(s)
        except Exception:
            s = None
        if s is None or not np.isfinite(s):
            continue
        buckets[sec].append(s)

    sector_score: Dict[str, float] = {}
    for sec, vals in buckets.items():
        if not vals:
            continue
        sector_score[sec] = float(sum(vals) / len(vals))
    return sector_score


def _sector_bias_map(items: Iterable[Any]) -> Dict[str, float]:
    """
    セクターごとの平均スコアから、
      ・上位グループ → +0.02
      ・下位グループ → -0.015
    のようなバイアステーブルを作る。
    """
    sec_score = _compute_sector_strength(items)
    if not sec_score or len(sec_score) <= 2:
        return {}

    # スコア順に並べる
    sorted_secs = sorted(sec_score.items(), key=lambda kv: kv[1], reverse=True)
    n = len(sorted_secs)
    top_n = max(1, n // 3)      # 上位1/3
    bottom_n = max(1, n // 4)   # 下位1/4

    bias: Dict[str, float] = {}
    for sec, _ in sorted_secs[:top_n]:
        bias[sec] = bias.get(sec, 0.0) + 0.02
    for sec, _ in sorted_secs[-bottom_n:]:
        bias[sec] = bias.get(sec, 0.0) - 0.015
    return bias


def apply_all(items: List[Any]) -> None:
    """
    PickItem 相当のオブジェクト配列に対して、
    ・時価総額によるサイズバイアス
    ・セクター平均スコアによるトレンドバイアス
    を加え、score / score_100 / stars を更新する。
    """
    if not items:
        return

    meta_map = _load_meta_for_items(items)
    sector_bias = _sector_bias_map(items)

    for it in items:
        code = str(getattr(it, "code", ""))
        raw_score = getattr(it, "score", None)
        try:
            base = float(raw_score) if raw_score is not None else 0.0
        except Exception:
            base = 0.0

        # サイズバイアス
        mc = None
        if code in meta_map:
            mc = meta_map[code].get("market_cap")
        b_size = _size_bias(mc)

        # セクターバイアス
        sec = getattr(it, "sector_display", None) or "UNKNOWN"
        b_sector = sector_bias.get(sec, 0.0)

        s_adj = _clamp01(base + b_size + b_sector)
        it.score = s_adj
        it.score_100 = int(round(s_adj * 100))

        if stars_from_score is not None:
            try:
                it.stars = int(stars_from_score(s_adj))
            except Exception:
                # 失敗しても落とさない
                pass