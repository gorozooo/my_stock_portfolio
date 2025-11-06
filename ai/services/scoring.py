from __future__ import annotations
from dataclasses import dataclass
import math

@dataclass
class Factors:
    daily_slope: float
    weekly_trend: float
    monthly_trend: float
    rs_index: float
    vol_spike: float
    confidence: float  # 0..1

def _z(x: float, s: float) -> float:
    # 簡易スケール（過剰に跳ねないよう抑える）
    return max(-3.0, min(3.0, x / s))

def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))

def compute_score(f: Factors) -> int:
    # 方向×強度をバランス良く（100点満点に正規化）
    z_d = _z(f.daily_slope,   s=abs(f.daily_slope)*0.5 + 0.5)
    z_w = _z(f.weekly_trend,  s=0.05)
    z_m = _z(f.monthly_trend, s=0.12)
    z_rs = _z(f.rs_index - 1.0, s=0.12)
    z_vs = _z(f.vol_spike - 1.0, s=0.6)

    raw = (
        0.22 * z_d +
        0.30 * z_w +
        0.26 * z_m +
        0.16 * z_rs +
        0.06 * z_vs
    )
    prob = _sigmoid(raw)  # 0..1
    score = int(round(100 * (0.7*prob + 0.3*f.confidence)))
    return max(0, min(100, score))

def compute_stars(score: int, conf: float) -> int:
    # 信頼度で閾値を微調整（MAX連発を抑える）
    base = [35, 55, 70, 82]  # 1,2,3,4 の閾値
    adj = (conf - 0.5) * 6   # ±3pt調整
    cuts = [c + adj for c in base]
    stars = 1
    if score >= cuts[0]: stars = 1
    if score >= cuts[1]: stars = 2
    if score >= cuts[2]: stars = 3
    if score >= cuts[3]: stars = 4
    if score >= (cuts[3] + 10): stars = 5
    return int(max(1, min(5, stars)))