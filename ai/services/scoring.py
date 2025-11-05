from __future__ import annotations
from typing import Dict, Optional

def compute_score(feature: Dict) -> int:
    """
    合成スコア（0-100）
      - 相対強度(rs_index) 〜 +20
      - 出来高ブースト(vol_boost) 〜 +15
      - 日/週/月の向き: up:+5, flat:0, down:-3（最大 +15）
    """
    base = 50
    rs = float(feature.get('strength', 1.0))
    vol = float(feature.get('vol_boost', 1.0))

    base += int(max(0, min(20, (rs - 1.0) * 40)))
    base += int(max(0, min(15, (vol - 1.0) * 20)))

    for k in ('trend_d','trend_w','trend_m'):
        v = feature.get(k)
        if v == 'up':
            base += 5
        elif v == 'flat':
            base += 0
        else:
            base -= 3

    return max(0, min(100, base))

def stars_from_confidence(confidence: Optional[float], fallback_ups: int = 0) -> int:
    """
    0.0〜1.0 の confidence → ⭐️1〜5
    未設定は「上向きの本数」(0〜3)に基づくフォールバック。
    """
    if confidence is not None and confidence > 0:
        v = 1 + round(confidence * 4)
        return max(1, min(5, v))
    v = max(1, min(5, 1 + int(fallback_ups)))
    return v

# 互換エイリアス（旧コードが compute_stars を import しても動く）
def compute_stars(confidence, fallback_ups: int = 0) -> int:
    return stars_from_confidence(confidence, fallback_ups)