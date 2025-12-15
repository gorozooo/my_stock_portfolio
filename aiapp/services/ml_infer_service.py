# aiapp/services/ml_infer_service.py
# -*- coding: utf-8 -*-
"""
ml_infer_service.py

目的:
- media/aiapp/ml/models/latest の LightGBM Booster(.txt) をロードし、
  picks_build から呼べる “推論1本化” を提供する。

出力:
- p_win: 勝つ確率（0..1）
- ev:    期待値（単位は学習時の y_r / y_pl 設計に依存。今は y_r 系を想定）
- hold_days_pred: 何日で決着しやすいか（任意モデル）
- tp_first: "tp_first" / "sl_first" / "none"（任意モデル）
- tp_first_probs: {"none":p, "tp_first":p, "sl_first":p}
- ml_rank: 並び替え用の統合スコア（C用）
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple, List

import numpy as np

try:
    import lightgbm as lgb
except Exception:  # pragma: no cover
    lgb = None  # type: ignore


# =========================================================
# dataclass
# =========================================================

@dataclass
class MLInferResult:
    p_win: Optional[float] = None
    ev: Optional[float] = None
    hold_days_pred: Optional[float] = None
    tp_first: Optional[str] = None
    tp_first_probs: Optional[Dict[str, float]] = None
    ml_rank: Optional[float] = None


# =========================================================
# helpers
# =========================================================

def _f(x: Any) -> Optional[float]:
    try:
        v = float(x)
        if not np.isfinite(v):
            return None
        return float(v)
    except Exception:
        return None


def _clamp01(v: Optional[float]) -> Optional[float]:
    if v is None:
        return None
    return float(max(0.0, min(1.0, float(v))))


def _safe_dict(d: Any) -> Dict[str, Any]:
    return d if isinstance(d, dict) else {}


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _sigmoid(x: float) -> float:
    try:
        return float(1.0 / (1.0 + np.exp(-float(x))))
    except Exception:
        return 0.5


# =========================================================
# loader cache
# =========================================================

_CACHE: Dict[str, Any] = {
    "loaded": False,
    "dir": None,
    "feature_cols": None,
    "label_maps": None,
    "meta": None,
    "m_pwin": None,
    "m_ev": None,
    "m_hold": None,
    "m_tp": None,
}


def _load_models(models_dir: Path) -> None:
    if _CACHE.get("loaded") and _CACHE.get("dir") == str(models_dir):
        return

    _CACHE.update(
        {
            "loaded": False,
            "dir": str(models_dir),
            "feature_cols": None,
            "label_maps": None,
            "meta": None,
            "m_pwin": None,
            "m_ev": None,
            "m_hold": None,
            "m_tp": None,
        }
    )

    if lgb is None:
        return

    feature_cols_path = models_dir / "feature_cols.json"
    label_maps_path = models_dir / "label_maps.json"
    meta_path = models_dir / "meta.json"

    m_pwin_path = models_dir / "model_pwin.txt"
    m_ev_path = models_dir / "model_ev.txt"
    m_hold_path = models_dir / "model_hold_days.txt"
    m_tp_path = models_dir / "model_tp_first.txt"

    feature_cols = _read_json(feature_cols_path)
    if isinstance(feature_cols, list):
        cols = [str(x) for x in feature_cols]
    else:
        cols = [str(x) for x in _safe_dict(feature_cols).get("feature_cols", [])]

    _CACHE["feature_cols"] = cols
    _CACHE["label_maps"] = _read_json(label_maps_path)
    _CACHE["meta"] = _read_json(meta_path)

    try:
        if m_pwin_path.exists():
            _CACHE["m_pwin"] = lgb.Booster(model_file=str(m_pwin_path))
    except Exception:
        _CACHE["m_pwin"] = None

    try:
        if m_ev_path.exists():
            _CACHE["m_ev"] = lgb.Booster(model_file=str(m_ev_path))
    except Exception:
        _CACHE["m_ev"] = None

    try:
        if m_hold_path.exists():
            _CACHE["m_hold"] = lgb.Booster(model_file=str(m_hold_path))
    except Exception:
        _CACHE["m_hold"] = None

    try:
        if m_tp_path.exists():
            _CACHE["m_tp"] = lgb.Booster(model_file=str(m_tp_path))
    except Exception:
        _CACHE["m_tp"] = None

    _CACHE["loaded"] = True


def _vectorize_last_row(feat_df, feature_cols: List[str]) -> Optional[np.ndarray]:
    """
    feat_df の最終行から、feature_cols の順で 1行ベクトルを作る（NaNは0埋め）。
    """
    try:
        if feat_df is None or len(feat_df) == 0:
            return None
        row = feat_df.iloc[-1]
        xs: List[float] = []
        for c in feature_cols:
            try:
                v = row.get(c)
            except Exception:
                v = None
            fv = _f(v)
            if fv is None:
                fv = 0.0
            xs.append(float(fv))
        arr = np.asarray(xs, dtype="float64").reshape(1, -1)
        return arr
    except Exception:
        return None


def _decode_tp_first_probs(probs: np.ndarray, label_maps: Dict[str, Any]) -> Tuple[Optional[str], Optional[Dict[str, float]]]:
    """
    label_maps.json の内容に揺れがあっても “それっぽく” 復元する。
    期待:
      - {"tp_first": {"none":0,"tp_first":1,"sl_first":2}} みたいな形
      - or {"tp_first_id2label": {"0":"none","1":"tp_first","2":"sl_first"}} みたいな形
    """
    try:
        p = np.asarray(probs, dtype="float64").ravel()
        if p.size == 0:
            return None, None

        lm = _safe_dict(label_maps)

        # 1) tp_first: {label->id}
        mapping = _safe_dict(lm.get("tp_first"))
        id2label: Dict[int, str] = {}

        if mapping:
            for k, v in mapping.items():
                try:
                    idx = int(v)
                    id2label[idx] = str(k)
                except Exception:
                    continue

        # 2) tp_first_id2label: {id->label}
        if not id2label:
            m2 = _safe_dict(lm.get("tp_first_id2label"))
            for k, v in m2.items():
                try:
                    idx = int(k)
                    id2label[idx] = str(v)
                except Exception:
                    continue

        # fallback: 0:none 1:tp_first 2:sl_first
        if not id2label:
            id2label = {0: "none", 1: "tp_first", 2: "sl_first"}

        probs_map: Dict[str, float] = {}
        for i in range(int(p.size)):
            lab = id2label.get(i, f"class_{i}")
            probs_map[str(lab)] = float(_clamp01(float(p[i])) or 0.0)

        best_i = int(np.argmax(p))
        best_lab = id2label.get(best_i, None)
        return (str(best_lab) if best_lab is not None else None), probs_map
    except Exception:
        return None, None


# =========================================================
# public API
# =========================================================

def infer_from_features(
    *,
    feat_df,
    models_root: str = "media/aiapp/ml/models/latest",
) -> MLInferResult:
    """
    feat_df（特徴量DataFrame）からML推論して返す。
    """
    models_dir = Path(models_root)
    if not models_dir.exists():
        return MLInferResult()

    _load_models(models_dir)

    feature_cols = _CACHE.get("feature_cols") or []
    if not isinstance(feature_cols, list) or len(feature_cols) == 0:
        return MLInferResult()

    x = _vectorize_last_row(feat_df, feature_cols)
    if x is None:
        return MLInferResult()

    # --- p_win ---
    p_win = None
    try:
        m = _CACHE.get("m_pwin")
        if m is not None:
            y = m.predict(x)
            # binary: shape (1,)
            p_win = _clamp01(_f(y[0] if isinstance(y, (list, np.ndarray)) else y))
    except Exception:
        p_win = None

    # --- ev ---
    ev = None
    try:
        m = _CACHE.get("m_ev")
        if m is not None:
            y = m.predict(x)
            ev = _f(y[0] if isinstance(y, (list, np.ndarray)) else y)
    except Exception:
        ev = None

    # --- hold_days_pred ---
    hold = None
    try:
        m = _CACHE.get("m_hold")
        if m is not None:
            y = m.predict(x)
            hold = _f(y[0] if isinstance(y, (list, np.ndarray)) else y)
            if hold is not None and hold < 0:
                hold = 0.0
    except Exception:
        hold = None

    # --- tp_first (multiclass) ---
    tp_first = None
    tp_probs = None
    try:
        m = _CACHE.get("m_tp")
        if m is not None:
            y = m.predict(x)
            # multiclass: shape (1, K)
            y_arr = np.asarray(y, dtype="float64")
            if y_arr.ndim == 2 and y_arr.shape[0] == 1:
                tp_first, tp_probs = _decode_tp_first_probs(y_arr[0], _CACHE.get("label_maps") or {})
    except Exception:
        tp_first, tp_probs = None, None

    # --- ml_rank（C用）---
    # いまは「EVを主役」にしつつ、p_win があれば軽く補強。
    ml_rank = None
    try:
        if ev is not None:
            base = float(ev)
            bump = 0.0
            if p_win is not None:
                # 0.5中心の微調整（大きく支配しない）
                bump = float(p_win - 0.5) * 0.20
            ml_rank = float(base + bump)
        elif p_win is not None:
            ml_rank = float(p_win)
    except Exception:
        ml_rank = None

    return MLInferResult(
        p_win=p_win,
        ev=ev,
        hold_days_pred=hold,
        tp_first=tp_first,
        tp_first_probs=tp_probs,
        ml_rank=ml_rank,
    )