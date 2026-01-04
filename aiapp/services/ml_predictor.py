# aiapp/services/ml_predictor.py
# -*- coding: utf-8 -*-
"""
ml_predictor.py（本番：ML推論ゲート / 軽量&安全）

目的
- train_lgbm_models で保存した latest モデルを読み込み
- picks_build から 1銘柄ずつ推論して、JSON(item) に載せる

返すもの（入れられる分だけ）
- p_win: 0..1（勝つ確率）
- ev:    期待値（学習ターゲット設計に依存：PL or R）
- hold_days_pred: 何日で決着しやすいか（任意）
- tp_first: TP先/SL先/none（任意）
- tp_first_probs: 各クラス確率（任意）

方針
- モデル/ファイルが無い・壊れてる → 例外を握りつぶして None 返し（LIVE/DEMO設計に合流）
- 全銘柄で毎回ロードしない（プロセス内キャッシュ）
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

try:
    import lightgbm as lgb
except Exception:  # pragma: no cover
    lgb = None  # type: ignore


# =========================================================
# data classes
# =========================================================

@dataclass
class MLPredictOut:
    p_win: Optional[float] = None
    ev: Optional[float] = None
    hold_days_pred: Optional[float] = None
    tp_first: Optional[str] = None
    tp_first_probs: Optional[Dict[str, float]] = None

    # diagnostics
    model_dir: Optional[str] = None
    ok: bool = False
    reason: Optional[str] = None


# =========================================================
# internal cache
# =========================================================

_CACHE: Dict[str, Any] = {
    "loaded": False,
    "model_dir": None,
    "feature_cols": None,     # List[str]
    "classes_tp_first": None, # List[str]
    "models": {},             # Dict[str, lgb.Booster]
}


# =========================================================
# utils
# =========================================================

def _clamp01(x: Any) -> Optional[float]:
    try:
        v = float(x)
    except Exception:
        return None
    if not np.isfinite(v):
        return None
    if v < 0.0:
        v = 0.0
    if v > 1.0:
        v = 1.0
    return float(v)


def _safe_float(x: Any) -> Optional[float]:
    try:
        v = float(x)
    except Exception:
        return None
    if not np.isfinite(v):
        return None
    return float(v)


def _norm_str(x: Any) -> str:
    return str(x or "").strip().lower()


def _load_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _find_latest_dir(base: Path) -> Optional[Path]:
    """
    media/aiapp/ml/models/latest があればそれを採用。
    無ければ base 配下のタイムスタンプディレクトリを新しい順に探す。
    """
    try:
        latest = base / "latest"
        if latest.exists() and latest.is_dir():
            return latest

        # fallback: 直下のディレクトリをmtime降順
        dirs = [p for p in base.iterdir() if p.is_dir()]
        if not dirs:
            return None
        dirs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return dirs[0]
    except Exception:
        return None


# =========================================================
# load
# =========================================================

def load_models_once(model_base_dir: str = "media/aiapp/ml/models") -> bool:
    """
    プロセス内で1回だけロードする。
    """
    if _CACHE.get("loaded"):
        return True

    if lgb is None:
        _CACHE["loaded"] = True
        _CACHE["model_dir"] = None
        _CACHE["feature_cols"] = None
        _CACHE["classes_tp_first"] = None
        _CACHE["models"] = {}
        return False

    base = Path(model_base_dir)
    mdir = _find_latest_dir(base)
    if mdir is None or not mdir.exists():
        _CACHE["loaded"] = True
        _CACHE["model_dir"] = None
        _CACHE["feature_cols"] = None
        _CACHE["classes_tp_first"] = None
        _CACHE["models"] = {}
        return False

    feature_cols: Optional[List[str]] = None
    classes_tp_first: Optional[List[str]] = None

    # feature_cols.json（必須扱い）
    fc = _load_json(mdir / "feature_cols.json")
    if isinstance(fc, dict) and isinstance(fc.get("feature_cols"), list):
        feature_cols = [str(x) for x in fc["feature_cols"] if str(x).strip()]
    elif isinstance(fc, list):
        feature_cols = [str(x) for x in fc if str(x).strip()]

    # tp_first クラス順（任意）
    cc = _load_json(mdir / "classes_tp_first.json")
    if isinstance(cc, dict) and isinstance(cc.get("classes"), list):
        classes_tp_first = [str(x) for x in cc["classes"] if str(x).strip()]
    elif isinstance(cc, list):
        classes_tp_first = [str(x) for x in cc if str(x).strip()]

    if not feature_cols:
        # feature_cols が無いなら ML を止める（入力列順が崩れるので危険）
        _CACHE["loaded"] = True
        _CACHE["model_dir"] = str(mdir)
        _CACHE["feature_cols"] = None
        _CACHE["classes_tp_first"] = classes_tp_first
        _CACHE["models"] = {}
        return False

    # モデルファイルは *.txt を拾う（LightGBM Booster）
    models: Dict[str, Any] = {}
    try:
        for p in sorted(mdir.glob("model_*.txt")):
            key = p.stem.replace("model_", "").strip().lower()
            if not key:
                continue
            try:
                booster = lgb.Booster(model_file=str(p))
                models[key] = booster
            except Exception:
                continue
    except Exception:
        models = {}

    # 既定のクラス順（無ければこの順で解釈）
    if not classes_tp_first:
        classes_tp_first = ["none", "tp_first", "sl_first"]

    _CACHE["loaded"] = True
    _CACHE["model_dir"] = str(mdir)
    _CACHE["feature_cols"] = feature_cols
    _CACHE["classes_tp_first"] = classes_tp_first
    _CACHE["models"] = models
    return True


# =========================================================
# feature build
# =========================================================

def _make_input_row(
    *,
    feat_df: pd.DataFrame,
    feature_cols: List[str],
    style: Optional[str] = None,
    horizon: Optional[str] = None,
    regime_label: Optional[str] = None,
) -> pd.DataFrame:
    """
    feature_cols の順で 1行 DataFrame を作る。
    - feat_df の最終行に同名列があればそれを使う
    - 無ければ style/horizon/regime_label のような文脈を入れる（列が存在する場合のみ）
    - それでも無ければ NaN
    """
    if feat_df is None or len(feat_df) == 0:
        base_row = {}
    else:
        try:
            last = feat_df.iloc[-1]
            base_row = last.to_dict() if hasattr(last, "to_dict") else {}
        except Exception:
            base_row = {}

    ctx: Dict[str, Any] = {
        "style": style,
        "horizon": horizon,
        "regime_label": regime_label,
    }

    out: Dict[str, Any] = {}
    for c in feature_cols:
        if c in base_row:
            out[c] = base_row.get(c)
            continue
        if c in ctx:
            out[c] = ctx.get(c)
            continue
        # ありがちな別名（学習側の命名ゆれ対策）
        if c == "mode_aggr":
            out[c] = style
            continue
        if c == "mode_period":
            out[c] = horizon
            continue

        out[c] = np.nan

    df = pd.DataFrame([out], columns=feature_cols)

    # 数値化できるものは数値化（文字列列はそのまま）
    for c in feature_cols:
        if df[c].dtype == object:
            # 数値っぽければ数値化、ダメならそのまま（カテゴリは学習側で処理済前提）
            try:
                df[c] = pd.to_numeric(df[c], errors="ignore")
            except Exception:
                pass

    return df


# =========================================================
# predict
# =========================================================

def predict_one(
    *,
    feat_df: pd.DataFrame,
    style: Optional[str] = None,
    horizon: Optional[str] = None,
    regime_label: Optional[str] = None,
    model_base_dir: str = "media/aiapp/ml/models",
) -> MLPredictOut:
    """
    1銘柄分の推論。
    """
    ok = load_models_once(model_base_dir=model_base_dir)

    model_dir = _CACHE.get("model_dir")
    feature_cols = _CACHE.get("feature_cols")
    models = _CACHE.get("models") or {}
    classes_tp_first = _CACHE.get("classes_tp_first") or ["none", "tp_first", "sl_first"]

    out = MLPredictOut(model_dir=model_dir, ok=False)

    if not ok or not feature_cols:
        out.reason = "ml_not_ready"
        return out

    # 入力行を作る
    X = _make_input_row(
        feat_df=feat_df,
        feature_cols=feature_cols,
        style=_norm_str(style) if style is not None else None,
        horizon=_norm_str(horizon) if horizon is not None else None,
        regime_label=str(regime_label) if regime_label is not None else None,
    )

    # p_win（分類）
    try:
        m = models.get("win") or models.get("pwin") or models.get("cls_win")
        if m is not None:
            pred = m.predict(X)
            # 2クラス想定：prob(win) が返る or shape=(1,)
            if isinstance(pred, (list, tuple, np.ndarray)):
                v = float(np.array(pred).ravel()[0])
                out.p_win = _clamp01(v)
    except Exception:
        pass

    # ev（回帰：R or PL）
    try:
        m = models.get("ev") or models.get("r") or models.get("pl") or models.get("reg_ev")
        if m is not None:
            pred = m.predict(X)
            if isinstance(pred, (list, tuple, np.ndarray)):
                v = float(np.array(pred).ravel()[0])
                out.ev = _safe_float(v)
    except Exception:
        pass

    # hold_days_pred（回帰）
    try:
        m = models.get("hold_days") or models.get("hold") or models.get("reg_hold_days")
        if m is not None:
            pred = m.predict(X)
            if isinstance(pred, (list, tuple, np.ndarray)):
                v = float(np.array(pred).ravel()[0])
                out.hold_days_pred = _safe_float(v)
    except Exception:
        pass

    # tp_first（多クラス）
    try:
        m = models.get("tp_first") or models.get("touch_tp_first") or models.get("cls_tp_first")
        if m is not None:
            pred = m.predict(X)
            arr = np.array(pred)
            if arr.ndim == 1:
                probs = arr.ravel().tolist()
            elif arr.ndim == 2:
                probs = arr[0].ravel().tolist()
            else:
                probs = []

            if probs and len(probs) == len(classes_tp_first):
                mp: Dict[str, float] = {}
                for cls, p in zip(classes_tp_first, probs):
                    v = _clamp01(p)
                    if v is None:
                        v = 0.0
                    mp[str(cls)] = float(v)
                out.tp_first_probs = mp

                # argmax
                best_cls = max(mp.items(), key=lambda kv: kv[1])[0]
                out.tp_first = str(best_cls)
    except Exception:
        pass

    out.ok = True
    out.reason = "ok"
    return out