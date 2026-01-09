# -*- coding: utf-8 -*-
"""
picks_build の共通ユーティリティ。

- Series/float の安全変換
- NaN -> None
- コード正規化
- mode_period / mode_aggr 変換
- score 0..100 化
- float or None
"""

from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd


def safe_series(x) -> pd.Series:
    """
    どんな形で来ても 1D pd.Series[float] に正規化する。
    """
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


def series_tail_to_list(s, max_points: int = 60):
    """
    pd.Series などから末尾 max_points 本だけ取り出して
    JSON 化しやすい Python の list[float | None] に変換する。
    NaN / inf は None にする。
    """
    ser = safe_series(s)
    if ser.empty:
        return None
    ser = ser.tail(max_points)

    out = []
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


def safe_float(x) -> float:
    """
    スカラ/Series/DataFrame/Index などから float を1つ取り出す。
    失敗時は NaN。
    """
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


def nan_to_none(x):
    if isinstance(x, (float, int)) and x != x:  # NaN
        return None
    return x


def score_to_0_100(s01: float) -> int:
    if not np.isfinite(s01):
        return 0
    return int(round(max(0.0, min(1.0, float(s01))) * 100))


def normalize_code(code: str) -> str:
    """
    DB/JSON でぶれないように銘柄コードを正規化。
    - "7203.T" → "7203"
    - "7203"   → "7203"
    """
    s = str(code or "").strip()
    if not s:
        return s
    if s.endswith(".T"):
        s = s[:-2]
    return s


def mode_period_from_horizon(horizon: str) -> str:
    """
    picks_build の horizon を BehaviorStats の mode_period に合わせる。
    """
    h = (horizon or "").strip().lower()
    if h in ("short", "mid", "long"):
        return h
    return "short"


def mode_aggr_from_style(style: str) -> str:
    """
    picks_build の style を BehaviorStats の mode_aggr に合わせる。
    """
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


def as_float_or_none(x) -> Optional[float]:
    try:
        if x is None:
            return None
        f = float(x)
        if not np.isfinite(f):
            return None
        return f
    except Exception:
        return None