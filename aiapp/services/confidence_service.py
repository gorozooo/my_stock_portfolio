# aiapp/services/confidence_service.py
# -*- coding: utf-8 -*-
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

import numpy as np
import pandas as pd

from aiapp.models.behavior_stats import BehaviorStats

try:
    from aiapp.services.scoring_service import score_sample, stars_from_score
except Exception:  # pragma: no cover
    score_sample = None  # type: ignore
    stars_from_score = None  # type: ignore


@dataclass
class ConfidenceDetail:
    stars_final: int

    # components
    stars_perf: Optional[int]
    stars_stability: int
    stars_distance: int
    stars_score: int

    # debug
    score01: Optional[float]

    perf_source: str               # "mode" / "all" / "none"
    perf_n: int
    perf_win_rate: Optional[float]
    perf_avg_pl: Optional[float]

    # weights
    w_perf: float
    w_stability: float
    w_distance: float
    w_score: float


def _clamp(x: float, lo: float, hi: float) -> float:
    return float(max(lo, min(hi, x)))


def _stars_from_winrate(win_rate: float, n: int, avg_pl: Optional[float]) -> int:
    if n < 5:
        return 1

    if avg_pl is not None and avg_pl < -3000:
        if win_rate >= 60:
            return 3
        if win_rate >= 50:
            return 2
        return 1

    if win_rate >= 70:
        return 5
    if win_rate >= 60:
        return 4
    if win_rate >= 50:
        return 3
    if win_rate >= 45:
        return 2
    return 1


def _stability_star(feat_df: pd.DataFrame) -> int:
    if feat_df is None or len(feat_df) < 30:
        return 2

    def _get(col: str) -> Optional[pd.Series]:
        try:
            s = feat_df.get(col)
        except Exception:
            s = None
        if s is None:
            return None
        try:
            s = pd.Series(s).astype("float64").dropna().tail(60)
        except Exception:
            return None
        return s if len(s) >= 20 else None

    s_slope = _get("SLOPE_20")
    s_ret = _get("RET_20")
    s_rsi = _get("RSI14")

    valid = [x for x in [s_slope, s_ret, s_rsi] if x is not None]
    if not valid:
        return 2

    def flips(s: pd.Series) -> float:
        v = s.values
        sign = np.sign(v)
        for i in range(1, len(sign)):
            if sign[i] == 0:
                sign[i] = sign[i - 1]
        f = np.sum(sign[1:] * sign[:-1] < 0)
        return float(f) / float(max(1, len(sign) - 1))

    flip_rates = [flips(s) for s in valid]
    fr = float(np.mean(flip_rates))

    if fr <= 0.08:
        return 5
    if fr <= 0.14:
        return 4
    if fr <= 0.22:
        return 3
    if fr <= 0.35:
        return 2
    return 1


def _distance_star(entry: Optional[float], tp: Optional[float], sl: Optional[float], atr: Optional[float]) -> int:
    if entry is None or tp is None or sl is None or atr is None:
        return 2
    try:
        atr = float(atr)
        if not np.isfinite(atr) or atr <= 0:
            return 2
        tp_d = abs(float(tp) - float(entry)) / atr
        sl_d = abs(float(entry) - float(sl)) / atr
    except Exception:
        return 2

    ok = 0
    if 0.5 <= tp_d <= 1.6:
        ok += 1
    if 0.4 <= sl_d <= 1.3:
        ok += 1

    if ok == 2:
        return 5
    if ok == 1:
        return 3
    return 1


def _score_star(feat_df: pd.DataFrame, regime: Optional[object]) -> Tuple[int, Optional[float]]:
    if score_sample is None or stars_from_score is None:
        return 2, None
    try:
        s01 = float(score_sample(feat_df, regime=regime))
        st = int(stars_from_score(s01))
        if 1 <= st <= 5:
            return st, s01
        return 2, s01
    except Exception:
        return 2, None


# -----------------------------
# BehaviorStats 取得（キャッシュ対応）
# -----------------------------

# cache key: (code, mode_period, mode_aggr)
# value: {"n":int, "win_rate":float|None, "avg_pl":float|None, "stars":int|None}
BehaviorCache = Dict[Tuple[str, str, str], Dict[str, Any]]


def _normalize_code(code: str) -> str:
    s = str(code or "").strip()
    if s.endswith(".T"):
        s = s[:-2]
    return s


def _get_perf_stats(
    code: str,
    mode_period: str,
    mode_aggr: str,
    *,
    behavior_cache: Optional[BehaviorCache] = None,
) -> Tuple[str, int, Optional[float], Optional[float], Optional[int]]:
    """
    優先順:
      1) code + mode_period + mode_aggr
      2) code + all/all
      3) none
    """
    code = _normalize_code(code)
    mp = (mode_period or "").strip().lower() or "all"
    ma = (mode_aggr or "").strip().lower() or "all"

    if behavior_cache is not None:
        row = behavior_cache.get((code, mp, ma))
        if row:
            n = int(row.get("n") or 0)
            wr = row.get("win_rate")
            ap = row.get("avg_pl")
            st = row.get("stars")
            return "mode", n, (float(wr) if wr is not None else None), (float(ap) if ap is not None else None), (int(st) if st is not None else None)

        row = behavior_cache.get((code, "all", "all"))
        if row:
            n = int(row.get("n") or 0)
            wr = row.get("win_rate")
            ap = row.get("avg_pl")
            st = row.get("stars")
            return "all", n, (float(wr) if wr is not None else None), (float(ap) if ap is not None else None), (int(st) if st is not None else None)

        return "none", 0, None, None, None

    # DB fallback（キャッシュ無しのときだけ）
    row = (
        BehaviorStats.objects
        .filter(code=code, mode_period=mp, mode_aggr=ma)
        .values("n", "win_rate", "avg_pl", "stars")
        .first()
    )
    if row:
        n = int(row.get("n") or 0)
        wr = row.get("win_rate")
        ap = row.get("avg_pl")
        st = row.get("stars")
        return "mode", n, (float(wr) if wr is not None else None), (float(ap) if ap is not None else None), (int(st) if st is not None else None)

    row = (
        BehaviorStats.objects
        .filter(code=code, mode_period="all", mode_aggr="all")
        .values("n", "win_rate", "avg_pl", "stars")
        .first()
    )
    if row:
        n = int(row.get("n") or 0)
        wr = row.get("win_rate")
        ap = row.get("avg_pl")
        st = row.get("stars")
        return "all", n, (float(wr) if wr is not None else None), (float(ap) if ap is not None else None), (int(st) if st is not None else None)

    return "none", 0, None, None, None


# -----------------------------
# public API
# -----------------------------

def compute_confidence_detail(
    *,
    code: str,
    feat_df: pd.DataFrame,
    entry: Optional[float],
    tp: Optional[float],
    sl: Optional[float],
    mode_period: str,
    mode_aggr: str,
    regime: Optional[object] = None,
    behavior_cache: Optional[BehaviorCache] = None,
) -> ConfidenceDetail:
    """
    ⭐️最終決定の内訳を返す（精度重視ハイブリッド）
    """
    perf_source, perf_n, perf_wr, perf_avg_pl, perf_st = _get_perf_stats(
        code, mode_period, mode_aggr, behavior_cache=behavior_cache
    )

    stars_perf: Optional[int] = None
    if isinstance(perf_st, int) and 1 <= perf_st <= 5:
        stars_perf = int(perf_st)
    elif perf_wr is not None:
        stars_perf = int(_stars_from_winrate(float(perf_wr), int(perf_n), perf_avg_pl))

    st_stability = int(_stability_star(feat_df))
    st_score, score01 = _score_star(feat_df, regime)

    atr = None
    try:
        if feat_df is not None and len(feat_df) > 0:
            if "ATR14" in feat_df.columns:
                atr = float(pd.Series(feat_df["ATR14"]).dropna().iloc[-1])
            elif "ATR" in feat_df.columns:
                atr = float(pd.Series(feat_df["ATR"]).dropna().iloc[-1])
    except Exception:
        atr = None

    st_distance = int(_distance_star(entry, tp, sl, atr))

    # ---- 重み（nで賢くなる）----
    w_perf = _clamp(0.10 + (float(perf_n) / 30.0) * 0.45, 0.10, 0.55)
    remain = 1.0 - w_perf

    # 精度重視：安定性/距離を厚め、スコアは補助
    w_stability = remain * 0.40
    w_distance = remain * 0.35
    w_score = remain * 0.25

    def n01(star: Optional[int]) -> float:
        if star is None:
            return 0.0
        return _clamp((float(star) - 1.0) / 4.0, 0.0, 1.0)

    s_perf = n01(stars_perf)
    s_stab = n01(st_stability)
    s_dist = n01(st_distance)
    s_scr = n01(st_score)

    score_final = (
        w_perf * s_perf
        + w_stability * s_stab
        + w_distance * s_dist
        + w_score * s_scr
    )
    stars_final = int(round(1.0 + 4.0 * _clamp(score_final, 0.0, 1.0)))
    stars_final = int(_clamp(stars_final, 1, 5))

    return ConfidenceDetail(
        stars_final=stars_final,
        stars_perf=stars_perf,
        stars_stability=st_stability,
        stars_distance=st_distance,
        stars_score=st_score,
        score01=score01,
        perf_source=perf_source,
        perf_n=int(perf_n),
        perf_win_rate=perf_wr,
        perf_avg_pl=perf_avg_pl,
        w_perf=float(w_perf),
        w_stability=float(w_stability),
        w_distance=float(w_distance),
        w_score=float(w_score),
    )


def compute_confidence_star(
    *,
    code: str,
    feat_df: pd.DataFrame,
    entry: Optional[float],
    tp: Optional[float],
    sl: Optional[float],
    mode_period: str,
    mode_aggr: str,
    regime: Optional[object] = None,
    behavior_cache: Optional[BehaviorCache] = None,
) -> int:
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