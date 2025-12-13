# aiapp/services/confidence_service.py
# -*- coding: utf-8 -*-
"""
confidence_service.py（本番：⭐️司令塔）

本番仕様:
- ⭐️は「BehaviorStats（同モード→無ければ all/all） + 特徴量の安定性 + Entry/TP/SL距離の適正 + scoring_service」を合成して確定
- BehaviorStats は picks_build 側で一括ロードした behavior_cache を渡せる（DB連打防止）
- n（試行数）が小さいときは perf の重みを自動で下げる（精度重視）

★拡張（今回A）:
- BehaviorStats から stability / design_q を取り込み、⭐️に“効かせる”
  - stability: feat_df由来の即時計算と、DB蓄積値を n に応じてブレンド
  - design_q : distance（Entry/TP/SLの妥当性）へ品質補正としてブレンド
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

    # from BehaviorStats（今回A）
    perf_stability01: Optional[float]
    perf_design_q01: Optional[float]

    # weights（合計1になるように正規化済み）
    w_perf: float
    w_stability: float
    w_distance: float
    w_score: float


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


def _safe01(x: Any) -> Optional[float]:
    try:
        if x is None:
            return None
        v = float(x)
        if not np.isfinite(v):
            return None
        return float(_clamp(v, 0.0, 1.0))
    except Exception:
        return None


def _stars_from_01(v01: Optional[float]) -> Optional[int]:
    """
    0..1 を ⭐️(1..5) に落とす共通変換。
    """
    if v01 is None:
        return None
    x = float(_clamp(v01, 0.0, 1.0))
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

    row_dict は少なくとも:
      {stars,n,win_rate,avg_pl}
    を想定しつつ、今回Aで:
      {stability, design_q}
    もあれば利用する。
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
            .values("stars", "n", "win_rate", "avg_pl", "stability", "design_q")
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
            .values("stars", "n", "win_rate", "avg_pl", "stability", "design_q")
            .first()
        )
        if r:
            return dict(r), "all"
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


def _stars_stability_from_feat(feat_df: pd.DataFrame) -> int:
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

        # RSIの張り付き（極端 or ずっと50付近）を軽く減点
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


def _stars_perf_from_behavior(row: Optional[Dict[str, Any]]) -> Tuple[Optional[int], int, Optional[float], Optional[float], Optional[float], Optional[float]]:
    """
    BehaviorStats 側の stars を採用しつつ、n が小さいときは「中立(3)へ寄せる」。
    戻り: (stars_perf, n, win_rate, avg_pl, stability01, design_q01)
    """
    if not row:
        return None, 0, None, None, None, None

    try:
        stars_raw = row.get("stars")
        n = int(row.get("n") or 0)
        win_rate = row.get("win_rate", None)
        avg_pl = row.get("avg_pl", None)

        stability01 = _safe01(row.get("stability"))
        design_q01 = _safe01(row.get("design_q"))

        if stars_raw is None:
            return None, n, win_rate, avg_pl, stability01, design_q01

        s = int(stars_raw)
        s = max(1, min(5, s))

        # n が少ないときは 3 に寄せる（精度重視）
        r = _sigmoid((n - 8) / 3.0)  # 0..1
        s_blend = (1.0 - r) * 3.0 + r * float(s)
        s_perf = int(round(_clamp(s_blend, 1.0, 5.0)))

        return (
            s_perf,
            n,
            (float(win_rate) if win_rate is not None else None),
            (float(avg_pl) if avg_pl is not None else None),
            stability01,
            design_q01,
        )
    except Exception:
        return None, 0, None, None, None, None


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

    # 2) behavior（同モード→all/all）
    row, src = _get_behavior_row(
        code=code,
        mode_period=mode_period,
        mode_aggr=mode_aggr,
        behavior_cache=behavior_cache,
    )
    stars_perf, perf_n, perf_wr, perf_pl, perf_stability01, perf_design_q01 = _stars_perf_from_behavior(row)

    # n による信頼度（0..1）
    # perf/stability/design_q を “育ち” に合わせて効かせるための共通係数
    r_grow = _sigmoid((perf_n - 8) / 3.0) if perf_n > 0 else 0.0

    # 3) stability（feat 由来）
    stars_stab_feat = _stars_stability_from_feat(feat_df)

    # 3b) stability（DB 蓄積値）→ n に応じてブレンド
    stars_stab_db = _stars_from_01(perf_stability01)
    if stars_stab_db is None:
        stars_stab = int(stars_stab_feat)
    else:
        s = (1.0 - r_grow) * float(stars_stab_feat) + r_grow * float(stars_stab_db)
        stars_stab = int(round(_clamp(s, 1.0, 5.0)))

    # 4) distance
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

    stars_dist_raw = _stars_distance(entry, tp, sl, atr)

    # 4b) design_q（DB）を distance に品質補正としてブレンド（今回A）
    # - design_q は「設計の良さ」なので、距離評価（distance）と相性がいい
    # - ただし n が小さい間は効かせすぎない（r_growで抑制）
    stars_design = _stars_from_01(perf_design_q01)
    if stars_design is None:
        stars_dist = int(stars_dist_raw)
    else:
        # distance を主、design を副（例: 75%:25%）で、育ち(r_grow)分だけ混ぜる
        mix = 0.25 * r_grow
        s = (1.0 - mix) * float(stars_dist_raw) + mix * float(stars_design)
        stars_dist = int(round(_clamp(s, 1.0, 5.0)))

    # =========================================================
    # weights（精度重視）
    # - perf は n で重みが変動（n少→弱い、n多→強い）
    # =========================================================
    w_perf_base = 0.35
    w_stab_base = 0.30
    w_dist_base = 0.20
    w_score_base = 0.15

    perf_r = r_grow  # 0..1

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
        stars_stability=int(stars_stab),
        stars_distance=int(stars_dist),
        stars_score=int(stars_score),
        score01=float(score01),
        perf_source=str(src),
        perf_n=int(perf_n),
        perf_win_rate=perf_wr,
        perf_avg_pl=perf_pl,
        perf_stability01=perf_stability01,
        perf_design_q01=perf_design_q01,
        w_perf=float(w_perf),
        w_stability=float(w_stab),
        w_distance=float(w_dist),
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