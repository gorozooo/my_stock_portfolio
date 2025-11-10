# -*- coding: utf-8 -*-
from __future__ import annotations
import math
import numpy as np
import pandas as pd
from typing import Dict, List, Tuple

from .policy_loader import PolicyLoader
from .regime_service import RegimeService

SignalVec = Dict[str, float]

def _safe_pct(a: pd.Series, n: int) -> float:
    try:
        v = a.pct_change(n).iloc[-1]
        return float(v) if pd.notna(v) and np.isfinite(v) else 0.0
    except Exception:
        return 0.0

def _vol_ratio(vol: pd.Series, n: int = 20) -> float:
    try:
        r = float(vol.iloc[-1] / (vol.rolling(n).mean().iloc[-1] + 1e-9))
        return r if np.isfinite(r) else 0.0
    except Exception:
        return 0.0

def _atr(df: pd.DataFrame, n: int = 14) -> float:
    try:
        tr1 = (df["high"] - df["low"]).abs()
        tr2 = (df["high"] - df["close"].shift(1)).abs()
        tr3 = (df["low"] - df["close"].shift(1)).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = tr.rolling(n).mean().iloc[-1]
        return float(atr) if pd.notna(atr) and np.isfinite(atr) else 0.0
    except Exception:
        return 0.0

def _z(x: float, mean: float, std: float) -> float:
    if std <= 1e-9:
        return 0.0
    return (x - mean) / std

class ScoringService:
    """
    総合得点（内部scoreとscore_100）を算出。
    - 個別銘柄の原始シグナル抽出
    - レジーム×モードの重みによる合成
    - Universe内パーセンタイルで0–100に正規化
    """

    def __init__(self, loader: PolicyLoader | None = None, regime: RegimeService | None = None):
        self.loader = loader or PolicyLoader()
        self.regime = regime or RegimeService()

    def compute_signals(self, df: pd.DataFrame) -> SignalVec:
        """
        df: 必須列 'close','high','low','volume'
        """
        c = df["close"]
        v = df["volume"]
        trend20 = _safe_pct(c, 20)   # 20日トレンド
        mom5    = _safe_pct(c, 5)    # 5日モメンタム
        rs20    = trend20 - (c.pct_change(20).mean() if pd.notna(c.pct_change(20).mean()) else 0.0)
        volr    = _vol_ratio(v, 20)
        atr     = _atr(df, 14)
        atr_inv = 0.0 if atr <= 0 else 1.0 / atr  # ボラ低いほどプラス

        # オーバーフロー抑制・スケール整形
        return {
            "trend20": float(max(min(trend20,  0.5), -0.5)),
            "mom5":    float(max(min(mom5,     0.5), -0.5)),
            "rs20":    float(max(min(rs20,     0.5), -0.5)),
            "volr":    float(max(min(volr,     5.0),  0.0)),
            "atr_inv": float(max(min(atr_inv,  1e3),  0.0)),
        }

    def aggregate_score(self, sig: SignalVec, mode: str, regime_name: str | None = None) -> float:
        regime_name = regime_name or self.regime.detect()
        w = self.loader.weights(regime_name, mode)
        # 未定義キーは0重みで無視
        score = (
            sig.get("trend20", 0.0) * float(w.get("trend20", 0.0)) +
            sig.get("rs20",    0.0) * float(w.get("rs20",    0.0)) +
            sig.get("mom5",    0.0) * float(w.get("mom5",    0.0)) +
            sig.get("volr",    0.0) * float(w.get("volr",    0.0)) +
            sig.get("atr_inv", 0.0) * float(w.get("atr_inv", 0.0))
        )
        return float(score)

    def to_percentile(self, scores: List[float]) -> List[int]:
        """
        Universe内の相対化。全同値でも“全て50点”にならないよう微小分散を付与。
        """
        if not scores:
            return []
        arr = np.array(scores, dtype=float)
        if np.allclose(arr, arr[0]):
            # 微小ノイズで順位付けし0–100に線形割当
            n = len(arr)
            return [int(round(100.0 * i / max(n - 1, 1))) for i in range(n)]
        # Z→CDFで滑らかに0–100へ
        m, s = float(arr.mean()), float(arr.std(ddof=0))
        z = np.array([_z(x, m, s) for x in arr])
        cdf = 0.5 * (1.0 + erf(z / math.sqrt(2.0)))  # 正規近似
        return [int(round(100.0 * float(x))) for x in cdf]

# math.erf が必要
from math import erf