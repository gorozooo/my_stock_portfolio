# -*- coding: utf-8 -*-
"""
1銘柄処理（価格→特徴量→フィルタ→スコア→Entry/TP/SL→⭐️→ML→理由→Sizing→PickItem化）。

重要:
- ユーザーが不安に思った import 3つ（get_prices / make_features+FeatureConfig / compute_position_sizing）は
  “ここ/feature生成の責務側” が必ず持つ。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from aiapp.services.fetch_price import get_prices
from aiapp.models.features import make_features, FeatureConfig
from aiapp.services.sizing_service import compute_position_sizing

from .settings import BUILD_LOG, CONF_DETAIL
from .schema import PickItem
from .utils import (
    as_float_or_none,
    nan_to_none,
    normalize_code,
    safe_float,
    safe_series,
    score_to_0_100,
    series_tail_to_list,
)
from .chart_service import extract_chart_ohlc
from .reasons_adapter import build_reasons_features
from .entry_reason_service import classify_entry_reason

# optional imports（無くても動く）
try:
    from aiapp.services.ml_infer_service import infer_from_features as ml_infer_from_features
except Exception:  # pragma: no cover
    ml_infer_from_features = None  # type: ignore

try:
    from aiapp.services.reasons import make_reasons as make_ai_reasons
except Exception:  # pragma: no cover
    make_ai_reasons = None  # type: ignore

try:
    from aiapp.services.scoring_service import (
        score_sample as ext_score_sample,
        stars_from_score as ext_stars_from_score,
    )
except Exception:  # pragma: no cover
    ext_score_sample = None  # type: ignore
    ext_stars_from_score = None  # type: ignore

try:
    from aiapp.services.entry_service import compute_entry_tp_sl as ext_entry_tp_sl
except Exception:  # pragma: no cover
    ext_entry_tp_sl = None  # type: ignore

try:
    from aiapp.services.picks_filters import FilterContext, check_all as picks_check_all
except Exception:  # pragma: no cover
    FilterContext = None  # type: ignore
    picks_check_all = None  # type: ignore

try:
    from aiapp.services.confidence_service import compute_confidence_star, compute_confidence_detail
except Exception:  # pragma: no cover
    compute_confidence_star = None  # type: ignore
    compute_confidence_detail = None  # type: ignore


def _fallback_score_sample(feat: pd.DataFrame) -> float:
    if feat is None or len(feat) == 0:
        return 0.0

    f = feat.copy()
    for c in ["RSI14", "RET_5", "RET_20", "SLOPE_5", "SLOPE_20"]:
        if c not in f.columns:
            f[c] = np.nan

    def nz(s: pd.Series) -> pd.Series:
        s = safe_series(s)
        if s.empty:
            return s
        m = float(s.mean())
        sd = float(s.std(ddof=0))
        if not np.isfinite(sd) or sd == 0:
            return pd.Series(np.zeros(len(s)), index=s.index)
        return (s - m) / sd

    def sig(v: float) -> float:
        try:
            return float(1.0 / (1.0 + np.exp(-float(v))))
        except Exception:
            return 0.5

    rsi = safe_float(nz(f["RSI14"]).iloc[-1])
    mom5 = safe_float(nz(f["RET_5"]).iloc[-1])
    mom20 = safe_float(nz(f["RET_20"]).iloc[-1])
    sl5 = safe_float(nz(f["SLOPE_5"]).iloc[-1])
    sl20 = safe_float(nz(f["SLOPE_20"]).iloc[-1])

    comp = (
        0.30 * sig(rsi)
        + 0.25 * sig(mom5)
        + 0.20 * sig(mom20)
        + 0.15 * sig(sl5)
        + 0.10 * sig(sl20)
    )
    return float(max(0.0, min(1.0, comp)))


def _fallback_stars(score01: float) -> int:
    if not np.isfinite(score01):
        return 1
    s = max(0.0, min(1.0, float(score01)))
    if s < 0.2:
        return 1
    if s < 0.4:
        return 2
    if s < 0.6:
        return 3
    if s < 0.8:
        return 4
    return 5


def _fallback_entry_tp_sl(last: float, atr: float):
    if not np.isfinite(last) or not np.isfinite(atr) or atr <= 0:
        return None, None, None
    entry = last + 0.05 * atr
    tp = entry + 0.80 * atr
    sl = entry - 0.60 * atr
    return float(entry), float(tp), float(sl)


def work_one(
    user,
    code: str,
    nbars: int,
    *,
    mode_period: str,
    mode_aggr: str,
    behavior_cache: Optional[Dict[Tuple[str, str, str], Dict[str, Any]]] = None,
    filter_stats: Optional[Dict[str, int]] = None,
    regime: Optional[object] = None,
) -> Optional[Tuple[PickItem, Dict[str, Any]]]:
    try:
        raw = get_prices(code, nbars=nbars, period="3y")
        if raw is None or len(raw) == 0:
            if BUILD_LOG:
                print(f"[picks_build] {code}: empty price")
            return None

        max_points = 260
        chart_open, chart_high, chart_low, chart_closes, chart_dates = extract_chart_ohlc(
            raw, max_points=max_points
        )

        cfg = FeatureConfig()
        feat = make_features(raw, cfg=cfg)
        if feat is None or len(feat) == 0:
            if BUILD_LOG:
                print(f"[picks_build] {code}: empty features")
            return None

        close_s = safe_series(feat.get("Close"))
        atr_s = safe_series(feat.get(f"ATR{cfg.atr_period}") if f"ATR{cfg.atr_period}" in feat else None)

        last = safe_float(close_s.iloc[-1] if len(close_s) else np.nan)
        atr = safe_float(atr_s.iloc[-1] if len(atr_s) else np.nan)

        ma_short_col = f"MA{cfg.ma_short}"
        ma_mid_col = f"MA{cfg.ma_mid}"
        ma_75_col = f"MA{cfg.ma_long}"
        ma_100_col = f"MA{cfg.ma_extra1}"
        ma_200_col = f"MA{cfg.ma_extra2}"
        rsi_col = f"RSI{cfg.rsi_period}"

        chart_ma_short = series_tail_to_list(feat.get(ma_short_col), max_points=max_points)
        chart_ma_mid = series_tail_to_list(feat.get(ma_mid_col), max_points=max_points)
        chart_ma_75 = series_tail_to_list(feat.get(ma_75_col), max_points=max_points)
        chart_ma_100 = series_tail_to_list(feat.get(ma_100_col), max_points=max_points)
        chart_ma_200 = series_tail_to_list(feat.get(ma_200_col), max_points=max_points)
        chart_vwap = series_tail_to_list(feat.get("VWAP"), max_points=max_points)
        chart_rsi = series_tail_to_list(feat.get(rsi_col), max_points=max_points)

        high_52w = safe_float(safe_series(feat["HIGH_52W"]).iloc[-1]) if "HIGH_52W" in feat.columns else None
        low_52w = safe_float(safe_series(feat["LOW_52W"]).iloc[-1]) if "LOW_52W" in feat.columns else None
        high_all = safe_float(safe_series(feat["HIGH_ALL"]).iloc[-1]) if "HIGH_ALL" in feat.columns else None
        low_all = safe_float(safe_series(feat["LOW_ALL"]).iloc[-1]) if "LOW_ALL" in feat.columns else None

        high_52w = nan_to_none(high_52w)
        low_52w = nan_to_none(low_52w)
        high_all = nan_to_none(high_all)
        low_all = nan_to_none(low_all)

        # filters
        if picks_check_all is not None and FilterContext is not None:
            try:
                ctx = FilterContext(code=str(code), feat=feat.iloc[-1].to_dict(), last=last, atr=atr)
                decision = picks_check_all(ctx)
                if decision and getattr(decision, "skip", False):
                    if filter_stats is not None:
                        reason = getattr(decision, "reason_code", None) or "SKIP"
                        filter_stats[reason] = filter_stats.get(reason, 0) + 1
                    if BUILD_LOG:
                        rc = getattr(decision, "reason_code", None)
                        rt = getattr(decision, "reason_text", None)
                        print(f"[picks_build] {code}: filtered out ({rc}) {rt}")
                    return None
            except Exception as ex:
                if filter_stats is not None:
                    filter_stats["filter_error"] = filter_stats.get("filter_error", 0) + 1
                if BUILD_LOG:
                    print(f"[picks_build] {code}: filter error {ex}")

        # score
        if ext_score_sample:
            try:
                s01 = float(ext_score_sample(feat, regime=regime))
            except TypeError:
                s01 = float(ext_score_sample(feat))
        else:
            s01 = _fallback_score_sample(feat)

        score100 = score_to_0_100(s01)

        # entry/tp/sl
        if ext_entry_tp_sl:
            e, t, s = ext_entry_tp_sl(last, atr, mode="aggressive", horizon="short")
        else:
            e, t, s = _fallback_entry_tp_sl(last, atr)

        code_norm = normalize_code(code)

        # fallback stars
        fallback_star = None
        if ext_stars_from_score:
            try:
                fallback_star = int(ext_stars_from_score(s01))
            except Exception:
                fallback_star = None
        if not isinstance(fallback_star, int) or not (1 <= fallback_star <= 5):
            fallback_star = _fallback_stars(s01)

        stars = int(fallback_star)

        conf_meta: Dict[str, Any] = {}
        if compute_confidence_star is not None:
            try:
                try:
                    stars = int(
                        compute_confidence_star(
                            code=str(code_norm),
                            feat_df=feat,
                            entry=e,
                            tp=t,
                            sl=s,
                            mode_period=mode_period,
                            mode_aggr=mode_aggr,
                            regime=regime,
                            behavior_cache=behavior_cache,
                        )
                    )
                except TypeError:
                    stars = int(
                        compute_confidence_star(
                            code=str(code_norm),
                            feat_df=feat,
                            entry=e,
                            tp=t,
                            sl=s,
                            mode_period=mode_period,
                            mode_aggr=mode_aggr,
                            regime=regime,
                        )
                    )

                if CONF_DETAIL and compute_confidence_detail is not None:
                    try:
                        try:
                            d = compute_confidence_detail(
                                code=str(code_norm),
                                feat_df=feat,
                                entry=e,
                                tp=t,
                                sl=s,
                                mode_period=mode_period,
                                mode_aggr=mode_aggr,
                                regime=regime,
                                behavior_cache=behavior_cache,
                            )
                        except TypeError:
                            d = compute_confidence_detail(
                                code=str(code_norm),
                                feat_df=feat,
                                entry=e,
                                tp=t,
                                sl=s,
                                mode_period=mode_period,
                                mode_aggr=mode_aggr,
                                regime=regime,
                            )
                        conf_meta = {
                            "stars_final": int(d.stars_final),
                            "stars_perf": d.stars_perf,
                            "stars_stability": int(d.stars_stability),
                            "stars_distance": int(d.stars_distance),
                            "stars_score": int(d.stars_score),
                            "score01": d.score01,
                            "perf_source": d.perf_source,
                            "perf_n": d.perf_n,
                            "perf_win_rate": d.perf_win_rate,
                            "perf_avg_pl": d.perf_avg_pl,
                            "w_perf": float(d.w_perf),
                            "w_stability": float(d.w_stability),
                            "w_distance": float(d.w_distance),
                            "w_score": float(d.w_score),
                        }
                    except Exception:
                        conf_meta = {}
            except Exception as ex:
                if BUILD_LOG:
                    print(f"[picks_build] confidence_service error for {code_norm}: {ex}")
                stars = int(fallback_star)

        # ML
        ml_p_win = None
        ml_ev = None
        ml_rank = None
        ml_hold = None
        ml_tp = None
        ml_tp_probs = None

        if ml_infer_from_features is not None:
            try:
                r = ml_infer_from_features(feat_df=feat)
                ml_p_win = getattr(r, "p_win", None)
                ml_ev = getattr(r, "ev", None)
                ml_rank = getattr(r, "ml_rank", None)
                ml_hold = getattr(r, "hold_days_pred", None)
                ml_tp = getattr(r, "tp_first", None)
                ml_tp_probs = getattr(r, "tp_first_probs", None)
            except Exception as ex:
                if BUILD_LOG:
                    print(f"[picks_build] ml_infer error for {code_norm}: {ex}")

        # reasons
        reason_lines = None
        reason_concern = None
        if make_ai_reasons is not None:
            try:
                reasons_feat = build_reasons_features(feat, last, atr)
                rs, concern = make_ai_reasons(reasons_feat)
                if rs:
                    reason_lines = list(rs[:5])
                if concern:
                    reason_concern = str(concern)
            except Exception as ex:
                if BUILD_LOG:
                    print(f"[picks_build] reasons error for {code_norm}: {ex}")

        entry_reason = classify_entry_reason(
            feat,
            last=nan_to_none(last),
            atr=nan_to_none(atr),
            entry=nan_to_none(e),
            tp=nan_to_none(t),
            sl=nan_to_none(s),
            ml_tp_first=ml_tp,
            ml_tp_probs=ml_tp_probs if isinstance(ml_tp_probs, dict) else None,
            reason_lines=reason_lines,
            reason_concern=reason_concern,
        )

        if BUILD_LOG:
            msg = (
                f"[picks_build] {code_norm} last={last} atr={atr} "
                f"score01={s01:.3f} score100={score100} stars={stars} "
                f"(period={mode_period} aggr={mode_aggr}) entry_reason={entry_reason}"
            )
            if conf_meta:
                msg += f" conf={conf_meta}"
            print(msg)

        item = PickItem(
            code=str(code_norm),
            last_close=nan_to_none(last),
            atr=nan_to_none(atr),
            entry=nan_to_none(e),
            tp=nan_to_none(t),
            sl=nan_to_none(s),
            score=nan_to_none(s01),
            score_100=int(score100),
            stars=int(stars),
            ml_p_win=nan_to_none(ml_p_win),
            ml_ev=nan_to_none(ml_ev),
            ml_rank=nan_to_none(ml_rank),
            ml_hold_days_pred=nan_to_none(ml_hold),
            ml_tp_first=ml_tp,
            ml_tp_first_probs=ml_tp_probs,
            reason_lines=reason_lines,
            reason_concern=reason_concern,
            entry_reason=str(entry_reason),
            chart_open=chart_open,
            chart_high=chart_high,
            chart_low=chart_low,
            chart_closes=chart_closes,
            chart_dates=chart_dates,
            chart_ma_short=chart_ma_short,
            chart_ma_mid=chart_ma_mid,
            chart_ma_75=chart_ma_75,
            chart_ma_100=chart_ma_100,
            chart_ma_200=chart_ma_200,
            chart_vwap=chart_vwap,
            chart_rsi=chart_rsi,
            high_52w=high_52w,
            low_52w=low_52w,
            high_all=high_all,
            low_all=low_all,
        )

        # p_tp_first
        p_tp_first = None
        try:
            if isinstance(ml_tp_probs, dict):
                v = ml_tp_probs.get("tp_first")
                if v is not None:
                    p_tp_first = float(v)
        except Exception:
            p_tp_first = None

        # sizing（互換維持）
        try:
            sizing = compute_position_sizing(
                user=user,
                code=str(code_norm),
                last_price=last,
                atr=atr,
                entry=e,
                tp=t,
                sl=s,
                p_tp_first=p_tp_first,
            )
        except TypeError:
            sizing = compute_position_sizing(
                user=user,
                code=str(code_norm),
                last_price=last,
                atr=atr,
                entry=e,
                tp=t,
                sl=s,
            )

        item.qty_rakuten = sizing.get("qty_rakuten")
        item.required_cash_rakuten = sizing.get("required_cash_rakuten")
        item.est_pl_rakuten = sizing.get("est_pl_rakuten")
        item.est_loss_rakuten = sizing.get("est_loss_rakuten")

        item.qty_matsui = sizing.get("qty_matsui")
        item.required_cash_matsui = sizing.get("required_cash_matsui")
        item.est_pl_matsui = sizing.get("est_pl_matsui")
        item.est_loss_matsui = sizing.get("est_loss_matsui")

        item.qty_sbi = sizing.get("qty_sbi")
        item.required_cash_sbi = sizing.get("required_cash_sbi")
        item.est_pl_sbi = sizing.get("est_pl_sbi")
        item.est_loss_sbi = sizing.get("est_loss_sbi")

        item.ev_net_rakuten = sizing.get("ev_net_rakuten")
        item.rr_net_rakuten = sizing.get("rr_net_rakuten")
        item.ev_net_matsui = sizing.get("ev_net_matsui")
        item.rr_net_matsui = sizing.get("rr_net_matsui")
        item.ev_net_sbi = sizing.get("ev_net_sbi")
        item.rr_net_sbi = sizing.get("rr_net_sbi")

        item.ev_true_rakuten = sizing.get("ev_true_rakuten")
        item.ev_true_matsui = sizing.get("ev_true_matsui")
        item.ev_true_sbi = sizing.get("ev_true_sbi")

        reasons_text = sizing.get("reasons_text")
        item.reasons_text = reasons_text if reasons_text else None

        item.reason_rakuten = sizing.get("reason_rakuten_msg") or ""
        item.reason_matsui = sizing.get("reason_matsui_msg") or ""
        item.reason_sbi = sizing.get("reason_sbi_msg") or ""

        sizing_meta = {
            "risk_pct": sizing.get("risk_pct"),
            "lot_size": sizing.get("lot_size"),
        }
        if conf_meta:
            sizing_meta["confidence_detail"] = conf_meta

        if BUILD_LOG:
            evr = as_float_or_none(item.ev_true_rakuten)
            qtyr = int(item.qty_rakuten or 0)
            print(f"[picks_build] {code_norm} EV_true_rakuten={evr} qty_rakuten={qtyr} pTP={p_tp_first}")

        return item, sizing_meta

    except Exception as e:
        print(f"[picks_build] work error for {code}: {e}")
        if filter_stats is not None:
            filter_stats["work_error"] = filter_stats.get("work_error", 0) + 1
        return None