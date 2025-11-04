from typing import Dict

def compute_score(feature: Dict) -> int:
    """
    簡易スコア：相対強度・出来高ブースト・日週月方向で加点（0-100）
    """
    base = 50
    base += int(max(0, min(20, feature.get('strength', 0.0) * 10)))   # 0〜20
    base += int(max(0, min(15, (feature.get('vol_boost', 1.0) - 1) * 20)))  # 0〜15
    for k in ('trend_d', 'trend_w', 'trend_m'):
        base += 5 if feature.get(k) == 'up' else (0 if feature.get(k) == 'flat' else -3)
    return max(0, min(100, base))


def compute_stars(feature: Dict) -> int:
    """
    ⭐️1-5：方向一貫性＋強度でざっくり。
    後で “過去の同条件の再現成績×データ量” に差し替え。
    """
    ups = sum(1 for k in ('trend_d','trend_w','trend_m') if feature.get(k) == 'up')
    strength = feature.get('strength', 0.0)
    score = ups + (1 if strength > 1.2 else 0)
    return max(1, min(5, score))  # 1〜5