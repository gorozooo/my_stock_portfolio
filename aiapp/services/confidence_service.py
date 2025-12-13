# aiapp/services/confidence_service.py
# -*- coding: utf-8 -*-
"""
confidence_service.py（本番：⭐️司令塔）

本番仕様（現行）:
- ⭐️は「BehaviorStats（同モード→無ければ all/all） + 特徴量の安定性 + Entry/TP/SL距離の適正 + scoring_service」を合成して確定
- BehaviorStats は picks_build 側で一括ロードした behavior_cache を渡せる（DB連打防止）
- n（試行数）が小さいときは perf の重みを自動で下げる（精度重視）

★追加（あなたの方針）:
- BehaviorStats に stability / design_q がある場合、それも参照して「おすすめ順（stars→stability→design_q→n）」の思想に寄せる
- ただし DB に列が無い環境でも落ちない（互換維持）
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

import numpy as np
import pandas as pd

# BehaviorStats（DB fallback 用）
try:
    from aiapp.models.behavior_stats import BehaviorStats
except Exception:  # pragma: no cover
    BehaviorStats = None  # type: ignore

# scoring_service（補助輪 / ハイブリッド）
try:
    from aiapp.services.scoring_service import score_sample as ext_score_sample, stars_from_score as ext_stars_from_score
except Exception:  # pragma: no cover
    ext_score_sample = None  # type: ignore
    ext_stars_from_score = None  # type: ignore


# =========================================================
# dataclass
# =========================================================

@dataclass
class ConfidenceDetail:
    stars_final: int

    # components（1..5 or None）
    stars_perf: Optional[int]
    stars_stability: int
    stars_distance: int
    stars_score: int

    # diagnostics
    score01: float
    perf_source: str  # "mode" / "all" / "none"
    perf_n: int
    perf_win_rate: Optional[float]
    perf_avg_pl: Optional[float]

    # weights（合計1になるように正規化済み）
    w_perf: float
    w_stability: float
    w_distance: float
    w_score: float

    # --- optional: behavior extras（DBに列があれば入る） ---
    perf_hist_stability: Optional[float] = None   # 0..1想定（なければNone）
    perf_hist_design_q: Optional[float] = None    # 0..1想定（なければNone）
    stars_hist_stability: Optional[int] = None    # 1..5
    stars_hist_design: Optional[int] = None       # 1..5


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


def _safe01(v: Any) -> Optional[float]:
    """
    0..1 の指標想定（stability/design_q など）
    """
    try:
        if v is None:
            return None
        f = float(v)
        if not np.isfinite(f):
            return None
        return float(_clamp(f, 0.0, 1.0))
    except Exception:
        return None


def _stars_from_01(x01: Optional[float]) -> Optional[int]:
    """
    0..1 を 1..5 に変換（score と同じ段階）
    """
    if x01 is None:
        return None
    x = float(_clamp(x01, 0.0, 1.0))
    if x < 0.20:
        return 1
    if x < 0.40:
        return 2
    if x < 0.60:
        return 3
    if x < 0.80:
        return 4
    return 5


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

    row_dict は {stars,n,win_rate,avg_pl, stability?, design_q?} を想定（キーが無くてもOK）
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

    def _db_fetch(period: str, aggr: str) -> Optional[Dict[str, Any]]:
        # ★列がある環境なら stability/design_q も取る（無い環境でも落ちない）
        try:
            r = (
                BehaviorStats.objects
                .filter(code=c, mode_period=period, mode_aggr=aggr)
                .values("stars", "n", "win_rate", "avg_pl", "stability", "design_q")
                .first()
            )
            if r:
                return dict(r)
        except Exception:
            # 列が無い / DBが違う / 互換など
            pass

        try:
            r = (
                BehaviorStats.objects
                .filter(code=c, mode_period=period, mode_aggr=aggr)
                .values("stars", "n", "win_rate", "avg_pl")
                .first()
            )
            if r:
                return dict(r)
        except Exception:
            pass

        return None

    try:
        r = _db_fetch(mp, ma)
        if r:
            return r, "mode"
    except Exception:
        pass

    try:
        r = _db_fetch("all", "all")
        if r:
            return r, "all"
    except Exception:
        pass

    return None, "none"


# =========================================================
# components（精度重視）
# =========================================================

def _stars_from_score(feat_df: pd.DataFrame, regime: Optional[object] = None) -> Tuple[int, float]:
    """
    scoring_service から (stars_score, score01) を取る。
    """
    score01 = 0.0
    if ext_score_sample is not None and feat_df is not None and len(feat_df) > 0:
        try:
            try:
                score01 = float(ext_score_sample(feat_df, regime=regime))
            except TypeError:
                score01 = float(ext_score_sample(feat_df))
        except Exception:
            score01 = 0.0
    score01 = _clamp(score01, 0.0, 1.0)

    if ext_stars_from_score is not None:
        try:
            s = int(ext_stars_from_score(score01))
            if 1 <= s <= 5:
                return s, score01
        except Exception:
            pass

    # fallback
    if score01 < 0.20:
        return 1, score01
    if score01 < 0.40:
        return 2, score01
    if score01 < 0.60:
        return 3, score01
    if score01 < 0.80:
        return 4, score01
    return 5, score01


def _stars_stability(feat_df: pd.DataFrame) -> int:
    """
    特徴量の安定性（精度重視版）
    - 直近60本の「SLOPE_20 と RET_20 の符号一致率」
    - RSI のブレ（50近傍に寄りすぎ / 極端に張り付き）を軽く減点
    """
    try:
        if feat_df is None or len(feat_df) < 30:
            return 3

        n = min(len(feat_df), 60)
        df = feat_df.tail(n)

        s20 = pd.to_numeric(df.get("SLOPE_20"), errors="coerce")
        r20 = pd.to_numeric(df.get("RET_20"), errors="coerce")
        rsi = pd.to_numeric(df.get("RSI14"), errors="coerce")

        ok = 0
        total = 0
        for a, b in zip(s20.tolist(), r20.tolist()):
            if not np.isfinite(a) or not np.isfinite(b):
                continue
            total += 1
            if (a >= 0 and b >= 0) or (a <= 0 and b <= 0):
                ok += 1

        if total == 0:
            base = 3
        else:
            ratio = ok / total  # 0..1
            if ratio >= 0.85:
                base = 5
            elif ratio >= 0.70:
                base = 4
            elif ratio >= 0.55:
                base = 3
            elif ratio >= 0.40:
                base = 2
            else:
                base = 1

        rr = rsi.dropna()
        if len(rr) >= 20:
            rmin = float(rr.min())
            rmax = float(rr.max())
            rstd = float(rr.std(ddof=0))
            if rmax >= 85 or rmin <= 15:
                base -= 1
            if rstd < 3.0:
                base -= 1

        return int(max(1, min(5, base)))
    except Exception:
        return 3


def _stars_distance(entry: Optional[float], tp: Optional[float], sl: Optional[float], atr: Optional[float]) -> int:
    """
    Entry/TP/SL 距離の適正（精度重視）
    - ATR を基準に TP/SL のバランスを評価
    - 想定RR (reward/risk) が 1.2 以上で加点
    - どれか欠ける/ATR不明は中立3
    """
    e = _nz(entry, np.nan)
    t = _nz(tp, np.nan)
    s = _nz(sl, np.nan)
    a = _nz(atr, np.nan)

    if not (np.isfinite(e) and np.isfinite(t) and np.isfinite(s) and np.isfinite(a) and a > 0):
        return 3

    reward = t - e
    risk = e - s
    if reward <= 0 or risk <= 0:
        return 1

    rr = reward / risk
    risk_atr = risk / a
    rew_atr = reward / a

    base = 3

    if rr >= 2.0:
        base += 2
    elif rr >= 1.2:
        base += 1
    elif rr < 0.9:
        base -= 1

    if risk_atr < 0.25:
        base -= 1
    if risk_atr > 2.5:
        base -= 1
    if rew_atr > 6.0:
        base -= 1

    return int(max(1, min(5, base)))


def _stars_perf_from_behavior(
    row: Optional[Dict[str, Any]]
) -> Tuple[Optional[int], int, Optional[float], Optional[float], Optional[float], Optional[float], Optional[int], Optional[int]]:
    """
    BehaviorStats 側の stars をベースにしつつ、stability/design_q があれば perf 内で合成して採用。
    さらに n が小さいときは「中立(3)へ寄せる」。

    戻り:
      (stars_perf, n, win_rate, avg_pl, hist_stab01, hist_design01, stars_hist_stab, stars_hist_design)
    """
    if not row:
        return None, 0, None, None, None, None, None, None

    try:
        stars_raw = row.get("stars")
        n = int(row.get("n") or 0)
        win_rate = row.get("win_rate", None)
        avg_pl = row.get("avg_pl", None)

        # optional extras（0..1想定）
        hist_stab01 = _safe01(row.get("stability"))
        hist_design01 = _safe01(row.get("design_q"))
        stars_hist_stab = _stars_from_01(hist_stab01)
        stars_hist_design = _stars_from_01(hist_design01)

        if stars_raw is None:
            return None, n, win_rate, avg_pl, hist_stab01, hist_design01, stars_hist_stab, stars_hist_design

        s = int(stars_raw)
        s = max(1, min(5, s))

        # perf 合成（おすすめ順：stars → stability → design_q）
        perf_components: list[float] = [float(s)]
        # stability/design_q は「補助輪」扱いで軽めに混ぜる
        if stars_hist_stab is not None:
            perf_components.append(float(stars_hist_stab) * 0.9)
        if stars_hist_design is not None:
            perf_components.append(float(stars_hist_design) * 0.8)

        s_mix = float(np.mean(perf_components)) if perf_components else float(s)
        s_mix = _clamp(s_mix, 1.0, 5.0)

        # n が少ないときは 3 に寄せる（精度重視）
        r = _sigmoid((n - 8) / 3.0)  # 0..1
        s_blend = (1.0 - r) * 3.0 + r * float(s_mix)
        s_perf = int(round(_clamp(s_blend, 1.0, 5.0)))

        return (
            s_perf,
            n,
            (float(win_rate) if win_rate is not None else None),
            (float(avg_pl) if avg_pl is not None else None),
            hist_stab01,
            hist_design01,
            stars_hist_stab,
            stars_hist_design,
        )
    except Exception:
        return None, 0, None, None, None, None, None, None


# =========================================================
# public API
# =========================================================

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
    behavior_cache: Optional[Dict[Tuple[str, str, str], Dict[str, Any]]] = None,
) -> ConfidenceDetail:
    """
    ⭐️の詳細（デバッグ/検証用）
    """
    # 1) scoring
    stars_score, score01 = _stars_from_score(feat_df, regime=regime)

    # 2) stability（特徴量）
    stars_stab = _stars_stability(feat_df)

    # 3) distance（Entry/TP/SL）
    atr = None
    try:
        if feat_df is not None and len(feat_df) > 0:
            last_row = feat_df.iloc[-1]
            for k in ("ATR14", "ATR_14", "ATR"):
                if k in feat_df.columns:
                    atr = float(pd.to_numeric(last_row.get(k), errors="coerce"))
                    break
    except Exception:
        atr = None
    stars_dist = _stars_distance(entry, tp, sl, atr)

    # 4) behavior（同モード→all/all）
    row, src = _get_behavior_row(
        code=code,
        mode_period=mode_period,
        mode_aggr=mode_aggr,
        behavior_cache=behavior_cache,
    )
    (
        stars_perf,
        perf_n,
        perf_wr,
        perf_pl,
        hist_stab01,
        hist_design01,
        stars_hist_stab,
        stars_hist_design,
    ) = _stars_perf_from_behavior(row)

    # =========================================================
    # weights（精度重視）
    # - perf は n で重みが変動（n少→弱い、n多→強い）
    # =========================================================
    w_perf_base = 0.35
    w_stab_base = 0.30
    w_dist_base = 0.20
    w_score_base = 0.15

    perf_r = _sigmoid((perf_n - 8) / 3.0) if perf_n > 0 else 0.0

    if stars_perf is None:
        w_perf = 0.0
    else:
        w_perf = w_perf_base * perf_r

    w_stab = w_stab_base
    w_dist = w_dist_base
    w_score = w_score_base

    tot = w_perf + w_stab + w_dist + w_score
    if tot <= 0:
        w_perf, w_stab, w_dist, w_score = 0.0, 0.5, 0.3, 0.2
        tot = 1.0

    w_perf /= tot
    w_stab /= tot
    w_dist /= tot
    w_score /= tot

    # =========================================================
    # final（ハイブリッド）
    # =========================================================
    perf_term = float(stars_perf) if stars_perf is not None else 3.0
    s_final = (
        w_perf * perf_term
        + w_stab * float(stars_stab)
        + w_dist * float(stars_dist)
        + w_score * float(stars_score)
    )
    stars_final = int(round(_clamp(s_final, 1.0, 5.0)))

    return ConfidenceDetail(
        stars_final=stars_final,
        stars_perf=stars_perf,
        stars_stability=stars_stab,
        stars_distance=stars_dist,
        stars_score=stars_score,
        score01=float(score01),
        perf_source=src,
        perf_n=int(perf_n),
        perf_win_rate=perf_wr,
        perf_avg_pl=perf_pl,
        w_perf=float(w_perf),
        w_stability=float(w_stab),
        w_distance=float(w_dist),
        w_score=float(w_score),
        perf_hist_stability=hist_stab01,
        perf_hist_design_q=hist_design01,
        stars_hist_stability=stars_hist_stab,
        stars_hist_design=stars_hist_design,
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
    behavior_cache: Optional[Dict[Tuple[str, str, str], Dict[str, Any]]] = None,
) -> int:
    """
    UI/本番用：⭐️だけ返す（高速）
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