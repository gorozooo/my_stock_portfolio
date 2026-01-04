# aiapp/services/ml_predict.py
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import numpy as np
import pandas as pd
from django.conf import settings

import lightgbm as lgb


@dataclass
class MLPredResult:
    ok: bool
    reason: str
    p_win: Optional[float] = None
    ev_pred: Optional[float] = None
    p_tp_first: Optional[float] = None
    p_sl_first: Optional[float] = None


def _safe_float(x: Any) -> Optional[float]:
    try:
        if x is None:
            return None
        f = float(x)
        if f != f:  # NaN
            return None
        return f
    except Exception:
        return None


def _model_latest_dir() -> Path:
    return Path(settings.MEDIA_ROOT) / "aiapp" / "ml" / "models" / "latest"


def _load_feature_cols(latest_dir: Path) -> list[str]:
    p = latest_dir / "feature_cols.json"
    cols = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(cols, list) or not cols:
        raise ValueError("feature_cols.json invalid")
    return [str(x) for x in cols]


def _load_booster(latest_dir: Path, fname: str) -> Optional[lgb.Booster]:
    p = latest_dir / fname
    if not p.exists():
        return None
    return lgb.Booster(model_file=str(p))


def _build_features_row(
    *,
    feature_cols: list[str],
    feat_last: Optional[Dict[str, Any]],
    score_100: Any,
    entry: Any,
    tp: Any,
    sl: Any,
) -> pd.DataFrame:
    """
    学習時の feature_cols.json を真実として、その列を1行で作る。
    取れない列は NaN / 0（*_id系）で埋める。
    """
    feat_last = feat_last or {}

    rsi14 = _safe_float(feat_last.get("RSI14"))
    bb_z = _safe_float(feat_last.get("BB_Z"))
    vwap_gap = _safe_float(feat_last.get("VWAP_GAP_PCT"))
    ret20 = _safe_float(feat_last.get("RET_20"))
    slope25 = _safe_float(feat_last.get("SLOPE_25"))
    atr14 = _safe_float(feat_last.get("ATR14"))

    e = _safe_float(entry)
    t = _safe_float(tp)
    s = _safe_float(sl)

    # design_rr / risk_atr / reward_atr
    design_rr = None
    risk_atr = None
    reward_atr = None
    if e is not None and t is not None and s is not None and atr14 is not None and atr14 > 0:
        reward_atr = (t - e) / atr14
        risk_atr = (e - s) / atr14
        if risk_atr and risk_atr > 0:
            design_rr = reward_atr / risk_atr

    # score_100
    sc100 = None
    try:
        sc100 = int(score_100) if score_100 is not None else None
    except Exception:
        sc100 = None

    base: Dict[str, Any] = {
        "ATR14": atr14,
        "SLOPE_25": slope25,
        "RET_20": ret20,
        "RSI14": rsi14,
        "BB_Z": bb_z,
        "VWAP_GAP_PCT": vwap_gap,
        "design_rr": design_rr,
        "risk_atr": risk_atr,
        "reward_atr": reward_atr,
        "score_100": sc100,
    }

    row: Dict[str, Any] = {}
    for c in feature_cols:
        if c in base:
            row[c] = base[c]
            continue

        # *_id は 0 埋め（学習側もint想定）
        if c.endswith("_id"):
            row[c] = 0
        else:
            row[c] = np.nan

    return pd.DataFrame([row], columns=feature_cols)


def predict_latest(
    *,
    feat_last: Optional[Dict[str, Any]],
    score_100: Any,
    entry: Any,
    tp: Any,
    sl: Any,
) -> MLPredResult:
    """
    latest モデルで
      - p_win（二値）
      - ev_pred（回帰）
      - p_tp_first（3クラスの class=tp_first の確率）
    を返す。
    """
    latest_dir = _model_latest_dir()
    try:
        if not latest_dir.exists():
            return MLPredResult(ok=False, reason=f"latest_dir_not_found:{latest_dir}")

        feat_cols = _load_feature_cols(latest_dir)

        m_pwin = _load_booster(latest_dir, "model_pwin.txt")
        m_ev = _load_booster(latest_dir, "model_ev.txt")
        m_tp = _load_booster(latest_dir, "model_tp_first.txt")  # optional

        if m_pwin is None or m_ev is None:
            return MLPredResult(ok=False, reason="model_missing(pwin/ev)")

        X = _build_features_row(
            feature_cols=feat_cols,
            feat_last=feat_last,
            score_100=score_100,
            entry=entry,
            tp=tp,
            sl=sl,
        )

        # p_win
        pwin = float(m_pwin.predict(X, num_iteration=m_pwin.best_iteration or m_pwin.current_iteration())[0])

        # ev_pred
        evp = float(m_ev.predict(X, num_iteration=m_ev.best_iteration or m_ev.current_iteration())[0])

        p_tp_first = None
        p_sl_first = None
        if m_tp is not None:
            proba = m_tp.predict(X, num_iteration=m_tp.best_iteration or m_tp.current_iteration())
            # multiclass: shape (1,3)
            if isinstance(proba, (list, np.ndarray)) and len(proba) > 0:
                arr = np.asarray(proba)[0]
                if arr.shape[0] >= 3:
                    # map: none=0, tp_first=1, sl_first=2
                    p_tp_first = float(arr[1])
                    p_sl_first = float(arr[2])

        return MLPredResult(
            ok=True,
            reason="ok",
            p_win=pwin,
            ev_pred=evp,
            p_tp_first=p_tp_first,
            p_sl_first=p_sl_first,
        )

    except Exception as e:
        return MLPredResult(ok=False, reason=f"exception:{type(e).__name__}")