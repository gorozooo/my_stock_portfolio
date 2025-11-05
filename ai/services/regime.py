from __future__ import annotations
from typing import Optional
from django.apps import apps

def _coerce_dir(val: Optional[object]) -> Optional[str]:
    """
    値を 'up'/'flat'/'down' に正規化。
    daily_slope が float の場合も考慮。
    """
    if val is None:
        return None
    if isinstance(val, str):
        s = val.strip().lower()
        if s in ('up', 'flat', 'down'):
            return s
        return None
    if isinstance(val, (int, float)):
        if val > 0:
            return 'up'
        elif val < 0:
            return 'down'
        return 'flat'
    return None

def calculate_market_regime() -> dict:
    """
    TrendResult の daily_slope / weekly_trend / monthly_trend から市場のレジームを算出。
    現時点では日足のみを主指標とする。
    """
    TrendResult = apps.get_model('ai', 'TrendResult')
    qs = TrendResult.objects.all()
    total = qs.count()
    if total == 0:
        return {'label': 'データ不足', 'confidence': 0}

    ups = flats = downs = 0
    for r in qs:
        d = _coerce_dir(getattr(r, 'daily_slope', None))
        if not d:
            continue
        if d == 'up':
            ups += 1
        elif d == 'down':
            downs += 1
        else:
            flats += 1

    valid = ups + downs + flats
    if valid == 0:
        return {'label': 'データ不足', 'confidence': 0}

    ratio = round(ups / valid * 100, 1)
    if ratio >= 70:
        label = '上昇'
    elif ratio >= 40:
        label = '中立'
    else:
        label = '下降'

    return {'label': label, 'confidence': ratio}