from typing import Dict, Optional

def compute_score(feature: Dict) -> int:
    """
    軽量な合成スコア（0-100）
      - 相対強度(rs_index)
      - 出来高ブースト(vol_boost)
      - 日/週/月の向き
    """
    base = 50
    base += int(max(0, min(20, (feature.get('strength', 1.0) - 1.0) * 40)))   # rs 1.0→0点, 1.5→20点
    base += int(max(0, min(15, (feature.get('vol_boost', 1.0) - 1.0) * 20)))  # 〜15
    for k in ('trend_d','trend_w','trend_m'):
        base += 5 if feature.get(k) == 'up' else (0 if feature.get(k) == 'flat' else -3)
    return max(0, min(100, base))

def stars_from_confidence(confidence: Optional[float], fallback_ups: int = 0) -> int:
    """
    0.0〜1.0 の confidence を ⭐️1〜5 にマップ。未設定は方向数(fallback_ups)で代替。
    """
    if confidence is not None and confidence > 0:
        # 0.00〜1.00 → 1〜5
        v = 1 + round(confidence * 4)  # 0.00→1, 0.25→2, 0.5→3, 0.75→4, 1.0→5
        return max(1, min(5, v))
    # フォールバック：上向きの本数でざっくり
    v = max(1, min(5, 1 + fallback_ups))
    return v