# ai/services/scoring.py
from __future__ import annotations
from dataclasses import dataclass
import math

@dataclass
class Factors:
    daily_slope: float = 0.0
    weekly_trend: float = 0.0
    monthly_trend: float = 0.0
    rs_index: float = 1.0         # 1.0 が中立
    vol_spike: float = 1.0        # 1.0 が中立
    confidence: float = 0.0       # 0.0–1.0

def _nz(x: float, d: float = 0.0) -> float:
    try:
        return float(x)
    except Exception:
        return float(d)

def _squash(x: float) -> float:
    """無限大レンジを -1..+1 に潰す。過大値で100点連発を防ぐ。"""
    return math.tanh(_nz(x))

def compute_score(f: Factors) -> int:
    """
    0..100 に正規化。
    - 週足・日足・月足の順で重み
    - RS/出来高は 1.0 が中立。そこからの差を tanh で圧縮
    - 最後に [0,1]→[0,100]
    """
    td = _squash(f.daily_slope)
    tw = _squash(f.weekly_trend)
    tm = _squash(f.monthly_trend)

    rs = _squash(_nz(f.rs_index) - 1.0)
    vs = _squash(_nz(f.vol_spike) - 1.0)

    raw = 0.36*tw + 0.26*td + 0.12*tm + 0.16*rs + 0.10*vs
    # raw は概ね [-1, +1] 付近に収まるので (raw+1)/2 で 0..1
    p = max(0.0, min(1.0, (raw + 1.0) / 2.0))

    # 信頼度を 0.85〜1.0 のレンジで微調整（高信頼を少しだけ押し上げ）
    conf = max(0.0, min(1.0, _nz(f.confidence)))
    p = p * (0.85 + 0.15 * conf)

    return int(round(p * 100))

def compute_stars(score: int, confidence: float) -> int:
    """
    ⭐️は 1..5。スコアと信頼度の両方で段階化（全部5★を防ぐ）。
    """
    s = max(0, min(100, int(score)))
    conf = max(0.0, min(1.0, _nz(confidence)))

    base = 1 if s < 40 else 2 if s < 55 else 3 if s < 70 else 4 if s < 85 else 5
    # 信頼度が低いなら 1 段階落とす、高いなら据え置き
    if conf < 0.35:
        base -= 1
    return max(1, min(5, base))