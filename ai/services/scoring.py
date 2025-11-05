from __future__ import annotations
from dataclasses import dataclass
from typing import Optional

# スコア用の中間表現（必要最小限）
@dataclass
class Factors:
    daily_slope: float            # 値幅の傾き（終値回帰の傾き）
    weekly_trend: float           # +1/0/-1
    monthly_trend: float          # +1/0/-1
    rs_index: float               # >1 強い
    vol_spike: float              # 1 が平常、>1 は出来高ブースト
    confidence: float             # 0〜1、データ量や整合で決定

def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))

def _norm_slope(slope: float) -> float:
    # 価格レンジ依存を吸収する簡易正規化：過剰影響を避けて-1..+1に収める
    # 体感で十分に効くようにスケール調整
    return _clamp(slope / 50.0, -1.0, 1.0)

def compute_score(f: Factors) -> int:
    """
    直感重視の合成スコア 0..100
    - トレンド（週・月）を主因に
    - 日足の傾き、RS、出来高ブーストで微調整
    - 信頼度が低い場合は減点
    """
    trend = (1.8 * f.weekly_trend + 1.2 * f.monthly_trend)  # -3..+3
    slope = 1.0 * _norm_slope(f.daily_slope)                # -1..+1
    rs = 0.8 * (f.rs_index - 1.0)                           # ≈ -∞..+∞ だが小さく寄与
    vol = 0.6 * (f.vol_spike - 1.0)

    base = 50.0 + 12.0 * trend + 10.0 * slope + 8.0 * rs + 6.0 * vol
    # 信頼度ペナルティ（0.6未満はきつめに）
    if f.confidence < 0.6:
        base -= (0.6 - f.confidence) * 25.0

    return int(round(_clamp(base, 0.0, 100.0)))

def compute_stars(score: int, confidence: Optional[float] = None) -> int:
    """
    スコアしきい値ベースの⭐️1..5
    信頼度が低いと1段階ダウン。高いと0.5段階相当を四捨五入で吸収。
    """
    if score >= 85: stars = 5
    elif score >= 75: stars = 4
    elif score >= 65: stars = 3
    elif score >= 55: stars = 2
    else: stars = 1

    if confidence is not None:
        if confidence < 0.55:
            stars -= 1
        elif confidence > 0.85 and score >= 70:
            stars += 0  # ここは見た目安定のため据え置き（増やし過ぎない）

    return max(1, min(5, int(round(stars))))