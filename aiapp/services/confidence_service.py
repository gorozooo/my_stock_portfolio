# -*- coding: utf-8 -*-
"""
confidence_service.py（本番：⭐️司令塔）

本番仕様（あなたの確定版）:
- ⭐️は「BehaviorStats のみ」で確定（scoring_service/テクニカルは一切使わない）
- BehaviorStats.stars は互換用に残しても “参照しない”
- BehaviorStats は picks_build 側で一括ロードした behavior_cache を渡せる（DB連打防止）
- n が小さい時は過信しない（ゲート/縮退で⭐️を抑える）
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

import numpy as np
import pandas as pd  # 呼び出し互換のため残す（使わない）

# BehaviorStats（DB fallback 用）
try:
    from aiapp.models.behavior_stats import BehaviorStats
except Exception:  # pragma: no cover
    BehaviorStats = None  # type: ignore


# =========================================================
# dataclass
# =========================================================

@dataclass
class ConfidenceDetail:
    stars_final: int

    # components（BehaviorStats onlyなので perf だけ）
    stars_perf: Optional[int]

    # diagnostics
    perf_source: str  # "mode" / "all" / "none"
    perf_n: int
    perf_win_rate: Optional[float]
    perf_avg_pl: Optional[float]
    perf_stability: Optional[float]
    perf_design_q: Optional[float]

    # gate/trace
    gate_perf_n: int
    stars_before_gate: Optional[int]
    stars_after_gate: Optional[int]


# =========================================================
# utils
# =========================================================

def _clamp(v: float, lo: float, hi: float) -> float:
    try:
        return float(max(lo, min(hi, float(v))))
    except Exception:
        return float(lo)


def _nz(v: Any, default: float = 0.0) -> float:
    try:
        f = float(v)
    except Exception:
        return float(default)
    if not np.isfinite(f):
        return float(default)
    return float(f)


def _norm_code(code: str) -> str:
    s = str(code or "").strip()
    if s.endswith(".T"):
        s = s[:-2]
    return s


def _norm_key(s: str) -> str:
    return str(s or "").strip().lower()


def _sigmoid(x: float) -> float:
    try:
        return float(1.0 / (1.0 + np.exp(-float(x))))
    except Exception:
        return 0.5


# =========================================================
# BehaviorStats lookup（cache優先 / DB fallback）
# =========================================================

def _get_behavior_row(
    *,
    code: str,
    mode_period: str,
    mode_aggr: str,
    behavior_cache: Optional[Dict[Tuple[str, str, str], Dict[str, Any]]] = None,
) -> Tuple[Optional[Dict[str, Any]], str]:
    """
    返り値:
      (row_dict, source)
      source: "mode" / "all" / "none"

    row_dict 想定キー（欠けてもOK）:
      {n, win_rate, avg_pl, stability, design_q}
    """
    c = _norm_code(code)
    mp = _norm_key(mode_period)
    ma = _norm_key(mode_aggr)

    # 1) cache（同モード）
    if behavior_cache:
        row = behavior_cache.get((c, mp, ma))
        if row is not None:
            return row, "mode"

        # 2) cache（all/all）
        row = behavior_cache.get((c, "all", "all"))
        if row is not None:
            return row, "all"

    # 3) DB fallback（同モード → all/all）
    if BehaviorStats is None:
        return None, "none"

    try:
        r = (
            BehaviorStats.objects
            .filter(code=c, mode_period=mp, mode_aggr=ma)
            .values("n", "win_rate", "avg_pl", "stability", "design_q")
            .first()
        )
        if r:
            return dict(r), "mode"
    except Exception:
        pass

    try:
        r = (
            BehaviorStats.objects
            .filter(code=c, mode_period="all", mode_aggr="all")
            .values("n", "win_rate", "avg_pl", "stability", "design_q")
            .first()
        )
        if r:
            return dict(r), "all"
    except Exception:
        pass

    return None, "none"


# =========================================================
# BehaviorStats only: perf⭐️
# =========================================================

def _stars_from_behavior_only(
    *,
    n: int,
    win_rate: Optional[float],
    avg_pl: Optional[float],
    stability: Optional[float],
    design_q: Optional[float],
) -> int:
    """
    BehaviorStatsのみで perf⭐️を作る（あなたの方針: AIの学習結果が主役）
    - 基本は win_rate 主導
    - stability / design_q は “同じ勝率なら設計が良い方を上げる” 程度の補助
    - avg_pl が大マイナスなら上限を抑える（地雷抑止）
    """
    n = int(n or 0)

    # win_rate が無い/変なら 0扱い
    try:
        wr = float(win_rate) if win_rate is not None else 0.0
    except Exception:
        wr = 0.0
    if not np.isfinite(wr):
        wr = 0.0
    wr = _clamp(wr, 0.0, 100.0)

    # stability / design_q は 0..1 想定（外れてても丸める）
    st = None
    dq = None
    if stability is not None:
        st = _clamp(_nz(stability, 0.0), 0.0, 1.0)
    if design_q is not None:
        dq = _clamp(_nz(design_q, 0.0), 0.0, 1.0)

    # まず win_rate で素の⭐️（わかりやすい基準）
    if wr >= 70:
        base = 5
    elif wr >= 60:
        base = 4
    elif wr >= 50:
        base = 3
    elif wr >= 45:
        base = 2
    else:
        base = 1

    # 設計の質で微調整（±1まで、やり過ぎない）
    # 目安: 0.70以上は加点候補、0.45以下は減点候補
    q = None
    if st is not None and dq is not None:
        q = 0.5 * st + 0.5 * dq
    elif st is not None:
        q = st
    elif dq is not None:
        q = dq

    if q is not None:
        if q >= 0.70 and base < 5:
            base += 1
        elif q <= 0.45 and base > 1:
            base -= 1

    # avg_pl が大きくマイナスなら上限抑制
    if avg_pl is not None:
        try:
            ap = float(avg_pl)
        except Exception:
            ap = 0.0
        if np.isfinite(ap) and ap < -3000:
            base = min(base, 3)

    return int(max(1, min(5, base)))


def _apply_n_gate(stars: int, n: int) -> int:
    """
    nが小さい時に過信しないゲート（育つAIの安全装置）
    - n < 5  : 上限2（“当たっても偶然”帯）
    - n < 10 : 上限3
    - n < 20 : 上限4
    - n >=20 : 制限なし
    """
    n = int(n or 0)
    s = int(stars or 1)

    if n < 5:
        return min(s, 2)
    if n < 10:
        return min(s, 3)
    if n < 20:
        return min(s, 4)
    return s


def _stars_perf_from_behavior(row: Optional[Dict[str, Any]]) -> Tuple[Optional[int], int, Optional[float], Optional[float], Optional[float], Optional[float]]:
    """
    BehaviorStatsのみから perf⭐️ を作る（stars列は使わない）
    戻り: (stars_perf, n, win_rate, avg_pl, stability, design_q)
    """
    if not row:
        return None, 0, None, None, None, None

    try:
        n = int(row.get("n") or 0)
        win_rate = row.get("win_rate", None)
        avg_pl = row.get("avg_pl", None)
        stability = row.get("stability", None)
        design_q = row.get("design_q", None)

        s_raw = _stars_from_behavior_only(
            n=n,
            win_rate=win_rate,
            avg_pl=avg_pl,
            stability=stability,
            design_q=design_q,
        )
        s_gated = _apply_n_gate(s_raw, n)

        return int(s_gated), int(n), (float(win_rate) if win_rate is not None else None), (float(avg_pl) if avg_pl is not None else None), (float(stability) if stability is not None else None), (float(design_q) if design_q is not None else None)
    except Exception:
        return None, 0, None, None, None, None


# =========================================================
# public API（呼び出し互換：引数は残すが使わない）
# =========================================================

def compute_confidence_detail(
    *,
    code: str,
    feat_df: pd.DataFrame,   # 互換のため残す（使わない）
    entry: Optional[float],  # 互換のため残す（使わない）
    tp: Optional[float],     # 互換のため残す（使わない）
    sl: Optional[float],     # 互換のため残す（使わない）
    mode_period: str,
    mode_aggr: str,
    regime: Optional[object] = None,  # 互換のため残す（使わない）
    behavior_cache: Optional[Dict[Tuple[str, str, str], Dict[str, Any]]] = None,
) -> ConfidenceDetail:
    """
    ⭐️詳細（デバッグ/検証用）
    ※ BehaviorStats only 固定
    """
    row, src = _get_behavior_row(
        code=code,
        mode_period=mode_period,
        mode_aggr=mode_aggr,
        behavior_cache=behavior_cache,
    )

    stars_perf, perf_n, perf_wr, perf_pl, perf_st, perf_dq = _stars_perf_from_behavior(row)

    # BehaviorStatsが無い場合は最低⭐️1（学習が無い＝信頼度の根拠が無い）
    stars_before = stars_perf
    if stars_perf is None:
        stars_final = 1
        stars_after = None
    else:
        stars_final = int(stars_perf)
        stars_after = int(stars_perf)

    return ConfidenceDetail(
        stars_final=int(stars_final),
        stars_perf=stars_perf,
        perf_source=str(src),
        perf_n=int(perf_n),
        perf_win_rate=perf_wr,
        perf_avg_pl=perf_pl,
        perf_stability=perf_st,
        perf_design_q=perf_dq,
        gate_perf_n=int(perf_n),
        stars_before_gate=stars_before,
        stars_after_gate=stars_after,
    )


def compute_confidence_star(
    *,
    code: str,
    feat_df: pd.DataFrame,   # 互換のため残す（使わない）
    entry: Optional[float],  # 互換のため残す（使わない）
    tp: Optional[float],     # 互換のため残す（使わない）
    sl: Optional[float],     # 互換のため残す（使わない）
    mode_period: str,
    mode_aggr: str,
    regime: Optional[object] = None,  # 互換のため残す（使わない）
    behavior_cache: Optional[Dict[Tuple[str, str, str], Dict[str, Any]]] = None,
) -> int:
    """
    UI/本番用：⭐️だけ返す（高速）
    ※ BehaviorStats only 固定
    """
    d = compute_confidence_detail(
        code=code,
        feat_df=feat_df,
        entry=entry,
        tp=tp,
        sl=sl,
        mode_period=mode_period,
        mode_aggr=mode_aggr,
        regime=regime,
        behavior_cache=behavior_cache,
    )
    return int(d.stars_final)