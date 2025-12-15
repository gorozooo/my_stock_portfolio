# aiapp/management/commands/picks_build.py
# -*- coding: utf-8 -*-
"""
AIピック生成コマンド（FULL + TopK + Sizing + 理由テキスト）

========================================
▼ 全体フロー（1銘柄あたり）
========================================
  1. 価格取得（OHLCV）
  2. 特徴量生成（テクニカル指標など）
  3. フィルタリング層（仕手株・流動性・異常値などで土台から除外）
  4. スコアリング
  5. ML推論（C: 主役）
  6. Entry / TP / SL の計算（★ML確率でRRターゲット制御）
  7. ⭐️算出（confidence_service）
  8. Sizing（数量・必要資金・想定PL/損失・見送り理由）
  9. 理由テキスト生成（選定理由×最大5行 + 懸念1行）
 10. バイアス層（セクター波 / 大型・小型バランスの微調整）
 11. ランキング（C: MLランク降順 → score_100降順 → 株価降順）→ JSON 出力
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone as dt_timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from aiapp.services.fetch_price import get_prices
from aiapp.models.features import make_features, FeatureConfig
from aiapp.services.sizing_service import compute_position_sizing

# ML infer（C: 主役）
try:
    from aiapp.services.ml_infer_service import infer_from_features as ml_infer_from_features
except Exception:  # pragma: no cover
    ml_infer_from_features = None  # type: ignore

# オプション扱いのサービス群（無くても動くように）
try:
    from aiapp.models import StockMaster
except Exception:  # pragma: no cover
    StockMaster = None  # type: ignore

try:
    from aiapp.services.reasons import make_reasons as make_ai_reasons
except Exception:  # pragma: no cover
    make_ai_reasons = None  # type: ignore

try:
    from aiapp.services.scoring_service import (
        score_sample as ext_score_sample,
        stars_from_score as ext_stars_from_score,  # 補助輪（最終⭐️はconfidence_service）
    )
except Exception:  # pragma: no cover
    ext_score_sample = None  # type: ignore
    ext_stars_from_score = None  # type: ignore

try:
    from aiapp.services.entry_service import compute_entry_tp_sl as ext_entry_tp_sl
except Exception:  # pragma: no cover
    ext_entry_tp_sl = None  # type: ignore

# 追加: フィルタ層 & バイアス層
try:
    from aiapp.services.picks_filters import FilterContext, check_all as picks_check_all
except Exception:  # pragma: no cover
    FilterContext = None  # type: ignore
    picks_check_all = None  # type: ignore

try:
    from aiapp.services.picks_bias import apply_all as apply_bias_all
except Exception:  # pragma: no cover
    apply_bias_all = None  # type: ignore

# 追加: マクロレジーム（あれば使う）
try:
    from aiapp.models.macro import MacroRegimeSnapshot
except Exception:  # pragma: no cover
    MacroRegimeSnapshot = None  # type: ignore

# ★追加: ⭐️司令塔（本番仕様）
try:
    from aiapp.services.confidence_service import compute_confidence_star, compute_confidence_detail
except Exception:  # pragma: no cover
    compute_confidence_star = None  # type: ignore
    compute_confidence_detail = None  # type: ignore

# ★追加: BehaviorStats（picks_buildで一括ロード→cache渡し）
try:
    from aiapp.models.behavior_stats import BehaviorStats
except Exception:  # pragma: no cover
    BehaviorStats = None  # type: ignore


# =========================================================
# 共通設定
# =========================================================

PICKS_DIR = Path("media/aiapp/picks")
PICKS_DIR.mkdir(parents=True, exist_ok=True)

JST = dt_timezone(timedelta(hours=9))


def dt_now_stamp() -> str:
    return datetime.now(JST).strftime("%Y%m%d_%H%M%S")


def _env_bool(key: str, default: bool = False) -> bool:
    v = os.getenv(key)
    if v is None:
        return default
    return str(v).strip().lower() in ("1", "true", "yes", "on")


BUILD_LOG = _env_bool("AIAPP_BUILD_LOG", False)
CONF_DETAIL = _env_bool("AIAPP_CONF_DETAIL", False)  # 1なら confidence_detail を meta に入れる（重いので通常OFF）


# =========================================================
# ヘルパ
# =========================================================

def _safe_series(x) -> pd.Series:
    if x is None:
        return pd.Series(dtype="float64")
    if isinstance(x, pd.Series):
        return x.astype("float64")
    if isinstance(x, pd.DataFrame):
        if x.shape[1] == 0:
            return pd.Series(dtype="float64")
        return x.iloc[:, -1].astype("float64")
    try:
        arr = np.asarray(x, dtype="float64")
        if arr.ndim == 0:
            return pd.Series([float(arr)], dtype="float64")
        return pd.Series(arr, dtype="float64")
    except Exception:
        return pd.Series(dtype="float64")


def _series_tail_to_list(s, max_points: int = 60) -> Optional[List[Optional[float]]]:
    ser = _safe_series(s)
    if ser.empty:
        return None
    ser = ser.tail(max_points)
    out: List[Optional[float]] = []
    for v in ser:
        try:
            f = float(v)
        except Exception:
            f = float("nan")
        if not np.isfinite(f):
            out.append(None)
        else:
            out.append(f)
    return out if out else None


def _safe_float(x) -> float:
    try:
        if x is None:
            return float("nan")
        if isinstance(x, (pd.Series, pd.Index)):
            if len(x) == 0:
                return float("nan")
            return float(pd.to_numeric(pd.Series(x).iloc[-1], errors="coerce"))
        if isinstance(x, pd.DataFrame):
            if x.shape[1] == 0 or len(x) == 0:
                return float("nan")
            col = x.columns[-1]
            return float(pd.to_numeric(x[col].iloc[-1], errors="coerce"))
        return float(x)
    except Exception:
        return float("nan")


def _nan_to_none(x):
    if isinstance(x, (float, int)) and x != x:
        return None
    return x


def _build_reasons_features(feat: pd.DataFrame, last: float, atr: float) -> Dict[str, Any]:
    if feat is None or len(feat) == 0:
        return {}

    row = feat.iloc[-1]

    def g(key: str) -> Optional[float]:
        try:
            v = row.get(key)
        except Exception:
            v = None
        if v is None:
            return None
        try:
            f = float(v)
        except Exception:
            return None
        if not np.isfinite(f):
            return None
        return f

    ema_slope = g("SLOPE_25") or g("SLOPE_20")

    rel_strength_10 = None
    r20 = g("RET_20")
    if r20 is not None:
        rel_strength_10 = r20 * 100.0

    ret1_pct = None
    r1 = g("RET_1")
    if r1 is not None:
        ret1_pct = r1 * 100.0

    rsi14 = g("RSI14")

    vol = g("Volume")
    ma_base = g("MA25") or g("MA20")
    vol_ma_ratio = None
    if vol is not None and ma_base is not None and ma_base > 0:
        vol_ma_ratio = vol / ma_base

    breakout_flag = 0
    gcross = g("GCROSS")
    if gcross is not None and gcross > 0:
        breakout_flag = 1

    vwap_proximity = g("VWAP_GAP_PCT")

    atr14 = None
    if np.isfinite(atr):
        atr14 = float(atr)

    last_price = None
    if np.isfinite(last):
        last_price = float(last)

    return {
        "ema_slope": ema_slope,
        "rel_strength_10": rel_strength_10,
        "ret1_pct": ret1_pct,
        "rsi14": rsi14,
        "vol_ma_ratio": vol_ma_ratio,
        "breakout_flag": breakout_flag,
        "atr14": atr14,
        "vwap_proximity": vwap_proximity,
        "last_price": last_price,
    }


def _extract_chart_ohlc(
    raw: pd.DataFrame,
    max_points: int = 60,
) -> Tuple[
    Optional[List[float]],
    Optional[List[float]],
    Optional[List[float]],
    Optional[List[float]],
    Optional[List[str]],
]:
    if raw is None:
        return None, None, None, None, None
    try:
        df = raw.copy()
    except Exception:
        return None, None, None, None, None

    if len(df) == 0:
        return None, None, None, None, None

    def col_name(candidates):
        for c in candidates:
            if c in df.columns:
                return c
        return None

    col_o = col_name(["Open", "open", "OPEN"])
    col_h = col_name(["High", "high", "HIGH"])
    col_l = col_name(["Low", "low", "LOW"])
    col_c = col_name(["Close", "close", "CLOSE"])

    if not (col_o and col_h and col_l and col_c):
        return None, None, None, None, None

    df = df[[col_o, col_h, col_l, col_c]].tail(max_points)

    opens = [float(v) for v in df[col_o].tolist()]
    highs = [float(v) for v in df[col_h].tolist()]
    lows = [float(v) for v in df[col_l].tolist()]
    closes = [float(v) for v in df[col_c].tolist()]

    if not closes:
        return None, None, None, None, None

    dates: List[str] = []
    try:
        if isinstance(df.index, pd.DatetimeIndex):
            dates = [d.strftime("%Y-%m-%d") for d in df.index]
        else:
            idx_dt = pd.to_datetime(df.index, errors="coerce")
            for d in idx_dt:
                if pd.isna(d):
                    dates.append("")
                else:
                    dates.append(d.strftime("%Y-%m-%d"))
    except Exception:
        dates = []

    return opens, highs, lows, closes, (dates or None)


# =========================================================
# フォールバック実装（サービスが無い場合）
# =========================================================

def _fallback_score_sample(feat: pd.DataFrame) -> float:
    if feat is None or len(feat) == 0:
        return 0.0

    f = feat.copy()
    for c in ["RSI14", "RET_5", "RET_20", "SLOPE_5", "SLOPE_20"]:
        if c not in f.columns:
            f[c] = np.nan

    def nz(s: pd.Series) -> pd.Series:
        s = _safe_series(s)
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

    rsi = _safe_float(nz(f["RSI14"]).iloc[-1])
    mom5 = _safe_float(nz(f["RET_5"]).iloc[-1])
    mom20 = _safe_float(nz(f["RET_20"]).iloc[-1])
    sl5 = _safe_float(nz(f["SLOPE_5"]).iloc[-1])
    sl20 = _safe_float(nz(f["SLOPE_20"]).iloc[-1])

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


def _fallback_entry_tp_sl(last: float, atr: float) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    if not np.isfinite(last) or not np.isfinite(atr) or atr <= 0:
        return None, None, None
    entry = last + 0.05 * atr
    tp = entry + 0.80 * atr
    sl = entry - 0.60 * atr
    return float(entry), float(tp), float(sl)


def _score_to_0_100(s01: float) -> int:
    if not np.isfinite(s01):
        return 0
    return int(round(max(0.0, min(1.0, s01)) * 100))


def _normalize_code(code: str) -> str:
    s = str(code or "").strip()
    if not s:
        return s
    if s.endswith(".T"):
        s = s[:-2]
    return s


def _mode_period_from_horizon(horizon: str) -> str:
    h = (horizon or "").strip().lower()
    if h in ("short", "mid", "long"):
        return h
    return "short"


def _mode_aggr_from_style(style: str) -> str:
    s = (style or "").strip().lower()
    if s in ("aggr", "norm", "def"):
        return s
    if s in ("aggressive", "attack", "atk"):
        return "aggr"
    if s in ("normal", "standard", "norm"):
        return "norm"
    if s in ("defensive", "defence", "def"):
        return "def"
    return "aggr"


# =========================================================
# 出力アイテム
# =========================================================

@dataclass
class PickItem:
    code: str
    name: Optional[str] = None
    sector_display: Optional[str] = None

    chart_open: Optional[List[float]] = None
    chart_high: Optional[List[float]] = None
    chart_low: Optional[List[float]] = None
    chart_closes: Optional[List[float]] = None
    chart_dates: Optional[List[str]] = None

    chart_ma_short: Optional[List[Optional[float]]] = None
    chart_ma_mid: Optional[List[Optional[float]]] = None
    chart_ma_75: Optional[List[Optional[float]]] = None
    chart_ma_100: Optional[List[Optional[float]]] = None
    chart_ma_200: Optional[List[Optional[float]]] = None
    chart_vwap: Optional[List[Optional[float]]] = None
    chart_rsi: Optional[List[Optional[float]]] = None

    high_52w: Optional[float] = None
    low_52w: Optional[float] = None
    high_all: Optional[float] = None
    low_all: Optional[float] = None

    last_close: Optional[float] = None
    atr: Optional[float] = None

    entry: Optional[float] = None
    tp: Optional[float] = None
    sl: Optional[float] = None

    score: Optional[float] = None
    score_100: Optional[int] = None
    stars: Optional[int] = None

    ml_p_win: Optional[float] = None
    ml_ev: Optional[float] = None
    ml_rank: Optional[float] = None
    ml_hold_days_pred: Optional[float] = None
    ml_tp_first: Optional[str] = None
    ml_tp_first_probs: Optional[Dict[str, float]] = None

    qty_rakuten: Optional[int] = None
    required_cash_rakuten: Optional[float] = None
    est_pl_rakuten: Optional[float] = None
    est_loss_rakuten: Optional[float] = None

    qty_matsui: Optional[int] = None
    required_cash_matsui: Optional[float] = None
    est_pl_matsui: Optional[float] = None
    est_loss_matsui: Optional[float] = None

    qty_sbi: Optional[int] = None
    required_cash_sbi: Optional[float] = None
    est_pl_sbi: Optional[float] = None
    est_loss_sbi: Optional[float] = None

    reasons_text: Optional[List[str]] = None

    reason_lines: Optional[List[str]] = None
    reason_concern: Optional[str] = None

    reason_rakuten: Optional[str] = None
    reason_matsui: Optional[str] = None
    reason_sbi: Optional[str] = None


# =========================================================
# 1銘柄処理
# =========================================================

def _work_one(
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
        chart_open, chart_high, chart_low, chart_closes, chart_dates = _extract_chart_ohlc(
            raw, max_points=max_points
        )

        cfg = FeatureConfig()
        feat = make_features(raw, cfg=cfg)
        if feat is None or len(feat) == 0:
            if BUILD_LOG:
                print(f"[picks_build] {code}: empty features")
            return None

        close_s = _safe_series(feat.get("Close"))
        atr_s = _safe_series(feat.get(f"ATR{cfg.atr_period}") if f"ATR{cfg.atr_period}" in feat else None)

        last = _safe_float(close_s.iloc[-1] if len(close_s) else np.nan)
        atr = _safe_float(atr_s.iloc[-1] if len(atr_s) else np.nan)

        ma_short_col = f"MA{cfg.ma_short}"
        ma_mid_col = f"MA{cfg.ma_mid}"
        ma_75_col = f"MA{cfg.ma_long}"
        ma_100_col = f"MA{cfg.ma_extra1}"
        ma_200_col = f"MA{cfg.ma_extra2}"
        rsi_col = f"RSI{cfg.rsi_period}"

        chart_ma_short = _series_tail_to_list(feat.get(ma_short_col), max_points=max_points)
        chart_ma_mid = _series_tail_to_list(feat.get(ma_mid_col), max_points=max_points)
        chart_ma_75 = _series_tail_to_list(feat.get(ma_75_col), max_points=max_points)
        chart_ma_100 = _series_tail_to_list(feat.get(ma_100_col), max_points=max_points)
        chart_ma_200 = _series_tail_to_list(feat.get(ma_200_col), max_points=max_points)
        chart_vwap = _series_tail_to_list(feat.get("VWAP"), max_points=max_points)
        chart_rsi = _series_tail_to_list(feat.get(rsi_col), max_points=max_points)

        high_52w = None
        low_52w = None
        high_all = None
        low_all = None

        if "HIGH_52W" in feat.columns:
            high_52w = _safe_float(_safe_series(feat["HIGH_52W"]).iloc[-1])
        if "LOW_52W" in feat.columns:
            low_52w = _safe_float(_safe_series(feat["LOW_52W"]).iloc[-1])
        if "HIGH_ALL" in feat.columns:
            high_all = _safe_float(_safe_series(feat["HIGH_ALL"]).iloc[-1])
        if "LOW_ALL" in feat.columns:
            low_all = _safe_float(_safe_series(feat["LOW_ALL"]).iloc[-1])

        high_52w = _nan_to_none(high_52w)
        low_52w = _nan_to_none(low_52w)
        high_all = _nan_to_none(high_all)
        low_all = _nan_to_none(low_all)

        # --- フィルタ層 ---
        if picks_check_all is not None and FilterContext is not None:
            try:
                ctx = FilterContext(
                    code=str(code),
                    feat=feat.iloc[-1].to_dict(),
                    last=last,
                    atr=atr,
                )
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

        # --- スコア ---
        if ext_score_sample:
            try:
                s01 = float(ext_score_sample(feat, regime=regime))
            except TypeError:
                s01 = float(ext_score_sample(feat))
        else:
            s01 = _fallback_score_sample(feat)

        score100 = _score_to_0_100(s01)

        # =========================================================
        # ★ ML推論（先にやる）
        # =========================================================
        code_norm = _normalize_code(code)

        ml_p_win = None
        ml_ev = None
        ml_rank = None
        ml_hold = None
        ml_tp = None
        ml_tp_probs = None
        ml_meta: Dict[str, Any] = {}

        if ml_infer_from_features is not None:
            try:
                r = ml_infer_from_features(feat_df=feat)
                ml_p_win = getattr(r, "p_win", None)
                ml_ev = getattr(r, "ev", None)
                ml_rank = getattr(r, "ml_rank", None)
                ml_hold = getattr(r, "hold_days_pred", None)
                ml_tp = getattr(r, "tp_first", None)
                ml_tp_probs = getattr(r, "tp_first_probs", None)
                ml_meta = {
                    "p_win": ml_p_win,
                    "ev": ml_ev,
                    "rank": ml_rank,
                    "hold": ml_hold,
                    "tp_first": ml_tp,
                }
            except Exception as ex:
                if BUILD_LOG:
                    print(f"[picks_build] ml_infer error for {code_norm}: {ex}")

        # =========================================================
        # ★ Entry/TP/SL（ML確率でRRターゲット制御）
        # =========================================================
        p_tp_first = None
        try:
            if isinstance(ml_tp_probs, dict):
                p_tp_first = ml_tp_probs.get("tp_first")
        except Exception:
            p_tp_first = None

        if ext_entry_tp_sl:
            e, t, s = ext_entry_tp_sl(
                last,
                atr,
                mode="aggressive",
                horizon="short",
                p_tp_first=p_tp_first,
            )
        else:
            e, t, s = _fallback_entry_tp_sl(last, atr)

        # =========================================================
        # ⭐️（confidence_service）
        # =========================================================
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

        # --- 理由5つ＋懸念 ---
        reason_lines: Optional[List[str]] = None
        reason_concern: Optional[str] = None
        if make_ai_reasons is not None:
            try:
                reasons_feat = _build_reasons_features(feat, last, atr)
                rs, concern = make_ai_reasons(reasons_feat)
                if rs:
                    reason_lines = list(rs[:5])
                if concern:
                    reason_concern = str(concern)
            except Exception as ex:
                if BUILD_LOG:
                    print(f"[picks_build] reasons error for {code_norm}: {ex}")

        if BUILD_LOG:
            msg = (
                f"[picks_build] {code_norm} last={last} atr={atr} "
                f"score01={s01:.3f} score100={score100} stars={stars} "
                f"(period={mode_period} aggr={mode_aggr})"
            )
            if ml_meta:
                msg += f" ml(p_win={ml_p_win} ev={ml_ev} hold={ml_hold} tp_first={ml_tp})"
            if conf_meta:
                msg += f" conf={conf_meta}"
            print(msg)

        item = PickItem(
            code=str(code_norm),
            last_close=_nan_to_none(last),
            atr=_nan_to_none(atr),
            entry=_nan_to_none(e),
            tp=_nan_to_none(t),
            sl=_nan_to_none(s),
            score=_nan_to_none(s01),
            score_100=int(score100),
            stars=int(stars),

            ml_p_win=_nan_to_none(ml_p_win),
            ml_ev=_nan_to_none(ml_ev),
            ml_rank=_nan_to_none(ml_rank),
            ml_hold_days_pred=_nan_to_none(ml_hold),
            ml_tp_first=ml_tp,
            ml_tp_first_probs=ml_tp_probs,

            reason_lines=reason_lines,
            reason_concern=reason_concern,
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

        # --- Sizing ---
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

        return item, sizing_meta

    except Exception as e:
        print(f"[picks_build] work error for {code}: {e}")
        if filter_stats is not None:
            filter_stats["work_error"] = filter_stats.get("work_error", 0) + 1
        return None


# =========================================================
# ユニバース読み込み
# =========================================================

def _load_universe_from_txt(name: str) -> List[str]:
    base = Path("aiapp/data/universe")
    filename = name
    if not filename.endswith(".txt"):
        filename = f"{filename}.txt"
    txt = base / filename
    if not txt.exists():
        print(f"[picks_build] universe file not found: {txt}")
        return []
    codes: List[str] = []
    for line in txt.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        codes.append(line.split(",")[0].strip())
    return codes


def _load_universe_all_jpx() -> List[str]:
    if StockMaster is None:
        print("[picks_build] StockMaster not available; ALL-JPX empty")
        return []
    try:
        qs = StockMaster.objects.values_list("code", flat=True).order_by("code")
        codes = [str(c).strip() for c in qs if c]
        print(f"[picks_build] ALL-JPX from StockMaster: {len(codes)} codes")
        return codes
    except Exception as e:
        print(f"[picks_build] ALL-JPX load error: {e}")
        return []


def _load_universe(name: str) -> List[str]:
    key = (name or "").strip().lower()

    if key in ("all_jpx", "all", "jpx_all"):
        codes = _load_universe_all_jpx()
        if codes:
            return codes
        print("[picks_build] ALL-JPX fallback to txt")
        return _load_universe_from_txt("all_jpx")

    if key in ("nk225", "nikkei225", "nikkei_225"):
        return _load_universe_from_txt("nk225")

    return _load_universe_from_txt(key)


# =========================================================
# 銘柄名・業種補完
# =========================================================

def _enrich_meta(items: List[PickItem]) -> None:
    if not items or StockMaster is None:
        return
    codes = [it.code for it in items if it and it.code]
    if not codes:
        return
    try:
        qs = StockMaster.objects.filter(code__in=codes).values("code", "name", "sector_name")
        meta: Dict[str, Tuple[str, str]] = {
            str(r["code"]): (r.get("name") or "", r.get("sector_name") or "")
            for r in qs
        }
        for it in items:
            if it.code in meta:
                nm, sec = meta[it.code]
                if not it.name:
                    it.name = nm or None
                if not it.sector_display:
                    it.sector_display = sec or None
    except Exception:
        pass


# =========================================================
# Django management command
# =========================================================

class Command(BaseCommand):
    help = "AIピック生成（FULL + TopK + Sizing + 理由テキキスト）"

    def add_arguments(self, parser):
        parser.add_argument(
            "--universe",
            type=str,
            default="nk225",
            help="all_jpx / nk225 / nikkei_225 / <file name> など",
        )
        parser.add_argument("--sample", type=int, default=None)
        parser.add_argument("--head", type=int, default=None)
        parser.add_argument("--budget", type=int, default=None)
        parser.add_argument("--nbars", type=int, default=260)
        parser.add_argument("--nbars-lite", type=int, default=45)
        parser.add_argument("--use-snapshot", action="store_true")
        parser.add_argument("--lite-only", action="store_true")
        parser.add_argument("--force", action="store_true")
        parser.add_argument("--style", type=str, default="aggressive")
        parser.add_argument("--horizon", type=str, default="short")
        parser.add_argument(
            "--topk",
            type=int,
            default=int(os.getenv("AIAPP_TOPK", "10")),
            help="上位何銘柄を latest_full.json に出すか",
        )

    def handle(self, *args, **opts):
        universe = opts.get("universe") or "nk225"
        nbars = int(opts.get("nbars") or 260)
        style = (opts.get("style") or "aggressive").lower()
        horizon = (opts.get("horizon") or "short").lower()
        topk = int(opts.get("topk") or 10)

        mode_period = _mode_period_from_horizon(horizon)
        mode_aggr = _mode_aggr_from_style(style)

        codes = _load_universe(universe)
        stockmaster_total = len(codes)

        macro_regime = None
        if MacroRegimeSnapshot is not None:
            try:
                today = datetime.now(JST).date()
                macro_regime = (
                    MacroRegimeSnapshot.objects
                    .filter(date__lte=today)
                    .order_by("-date")
                    .first()
                )
                if BUILD_LOG and macro_regime is not None:
                    print(
                        f"[picks_build] use MacroRegimeSnapshot "
                        f"date={macro_regime.date} regime={macro_regime.regime_label}"
                    )
            except Exception as ex:
                if BUILD_LOG:
                    print(f"[picks_build] macro regime load error: {ex}")

        if not codes:
            print("[picks_build] universe empty → 空JSON出力")

            regime_date_str = None
            if macro_regime is not None:
                d = getattr(macro_regime, "date", None)
                if d is not None:
                    regime_date_str = d.isoformat()

            self._emit(
                [],
                [],
                mode="full",
                style=style,
                horizon=horizon,
                universe=universe,
                topk=topk,
                meta_extra={
                    "stockmaster_total": stockmaster_total,
                    "filter_stats": {},
                    "regime_date": regime_date_str,
                    "regime_label": getattr(macro_regime, "regime_label", None) if macro_regime else None,
                    "regime_summary": getattr(macro_regime, "summary", None) if macro_regime else None,
                    "stars_engine": "confidence_service",
                    "stars_mode_period": mode_period,
                    "stars_mode_aggr": mode_aggr,
                    "behaviorstats_cache_rows": 0,
                    "ml_engine": "lightgbm",
                    "ml_models_dir": "media/aiapp/ml/models/latest",
                    "rank_mode": "C_ml_rank",
                },
            )
            return

        if BUILD_LOG:
            print(f"[picks_build] start FULL universe={universe} codes={stockmaster_total}")

        User = get_user_model()
        user = User.objects.first()

        items: List[PickItem] = []
        meta_extra: Dict[str, Any] = {}

        filter_stats: Dict[str, int] = {}
        first_conf_detail: Optional[Dict[str, Any]] = None

        behavior_cache: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
        if BehaviorStats is not None and codes:
            try:
                codes_norm = [_normalize_code(c) for c in codes if c]
                qs = (
                    BehaviorStats.objects
                    .filter(code__in=codes_norm)
                    .values("code", "mode_period", "mode_aggr", "stars", "n", "win_rate", "avg_pl")
                )
                for r in qs:
                    c = _normalize_code(r.get("code"))
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
                if BUILD_LOG:
                    print(f"[picks_build] BehaviorStats cache rows: {len(behavior_cache)}")
            except Exception as ex:
                if BUILD_LOG:
                    print(f"[picks_build] BehaviorStats cache load error: {ex}")
                behavior_cache = {}

        for code in codes:
            res = _work_one(
                user,
                code,
                nbars=nbars,
                mode_period=mode_period,
                mode_aggr=mode_aggr,
                behavior_cache=behavior_cache,
                filter_stats=filter_stats,
                regime=macro_regime,
            )
            if res is None:
                continue
            item, sizing_meta = res
            items.append(item)

            if sizing_meta:
                if sizing_meta.get("risk_pct") is not None and "risk_pct" not in meta_extra:
                    meta_extra["risk_pct"] = float(sizing_meta["risk_pct"])
                if sizing_meta.get("lot_size") is not None and "lot_size" not in meta_extra:
                    meta_extra["lot_size"] = int(sizing_meta["lot_size"])

                if CONF_DETAIL and first_conf_detail is None and sizing_meta.get("confidence_detail"):
                    first_conf_detail = sizing_meta.get("confidence_detail")

        _enrich_meta(items)

        if apply_bias_all is not None and items:
            try:
                apply_bias_all(items)
            except Exception as ex:
                if BUILD_LOG:
                    print(f"[picks_build] bias error: {ex}")

        def _rank_key(x: PickItem):
            mr = x.ml_rank if x.ml_rank is not None else -1e18
            sc = x.score_100 if x.score_100 is not None else -1e18
            lc = x.last_close if x.last_close is not None else -1e18
            return (mr, sc, lc)

        items.sort(key=_rank_key, reverse=True)

        top_items = items[: max(0, topk)]

        if BUILD_LOG:
            print(
                f"[picks_build] done stockmaster_total={stockmaster_total} "
                f"total={len(items)} topk={len(top_items)}"
            )

        meta_extra["stockmaster_total"] = stockmaster_total
        meta_extra["filter_stats"] = filter_stats

        if macro_regime is not None:
            d = getattr(macro_regime, "date", None)
            regime_date_str = d.isoformat() if d is not None else None
            meta_extra["regime_date"] = regime_date_str
            meta_extra["regime_label"] = getattr(macro_regime, "regime_label", None)
            meta_extra["regime_summary"] = getattr(macro_regime, "summary", None)

        meta_extra["stars_engine"] = "confidence_service"
        meta_extra["stars_mode_period"] = mode_period
        meta_extra["stars_mode_aggr"] = mode_aggr
        meta_extra["behaviorstats_cache_rows"] = len(behavior_cache)
        if first_conf_detail is not None:
            meta_extra["confidence_detail_sample"] = first_conf_detail

        meta_extra["ml_engine"] = "lightgbm"
        meta_extra["ml_models_dir"] = "media/aiapp/ml/models/latest"
        meta_extra["rank_mode"] = "C_ml_rank"

        self._emit(
            items,
            top_items,
            mode="full",
            style=style,
            horizon=horizon,
            universe=universe,
            topk=topk,
            meta_extra=meta_extra,
        )

    def _emit(
        self,
        all_items: List[PickItem],
        top_items: List[PickItem],
        *,
        mode: str,
        style: str,
        horizon: str,
        universe: str,
        topk: int,
        meta_extra: Dict[str, Any],
    ) -> None:
        meta: Dict[str, Any] = {
            "mode": mode,
            "style": style,
            "horizon": horizon,
            "universe": universe,
            "total": len(all_items),
            "topk": topk,
        }
        meta.update({k: v for k, v in (meta_extra or {}).items() if v is not None})

        data_all = {"meta": meta, "items": [asdict(x) for x in all_items]}
        data_top = {"meta": meta, "items": [asdict(x) for x in top_items]}

        PICKS_DIR.mkdir(parents=True, exist_ok=True)

        out_all_latest = PICKS_DIR / "latest_full_all.json"
        out_all_stamp = PICKS_DIR / f"{dt_now_stamp()}_{horizon}_{style}_full_all.json"
        out_all_latest.write_text(json.dumps(data_all, ensure_ascii=False, separators=(",", ":")))
        out_all_stamp.write_text(json.dumps(data_all, ensure_ascii=False, separators=(",", ":")))

        out_top_latest = PICKS_DIR / "latest_full.json"
        out_top_stamp = PICKS_DIR / f"{dt_now_stamp()}_{horizon}_{style}_full.json"
        out_top_latest.write_text(json.dumps(data_top, ensure_ascii=False, separators=(",", ":")))
        out_top_stamp.write_text(json.dumps(data_top, ensure_ascii=False, separators=(",", ":")))