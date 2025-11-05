from __future__ import annotations
from typing import Optional
from django.apps import apps

def _coerce_dir(val: Optional[object]) -> Optional[str]:
    """数値や文字列を up/flat/down に正規化"""
    if val is None: return None
    if isinstance(val, str):
        s = val.strip().lower()
        if s in ('up','flat','down'): return s
        return None
    if isinstance(val, (int,float)):
        if val > 0: return 'up'
        if val < 0: return 'down'
        return 'flat'
    return None

def _label(ratio: float) -> str:
    if ratio >= 70: return '上昇'
    if ratio >= 40: return '中立'
    return '下降'

def _analyze_field(field: str) -> dict:
    """TrendResult の特定フィールド（daily_slope等）から上昇率を算出"""
    TrendResult = apps.get_model('ai', 'TrendResult')
    qs = TrendResult.objects.all().exclude(**{field: None})
    total = qs.count()
    if total == 0:
        return {'label': 'データ不足', 'ratio': 0.0}

    ups = 0
    for r in qs:
        d = _coerce_dir(getattr(r, field))
        if d == 'up': ups += 1
    ratio = round(ups / total * 100, 1)
    return {'label': _label(ratio), 'ratio': ratio}

def calculate_market_regime() -> dict:
    """日・週・月レジームをまとめて返す"""
    daily = _analyze_field('daily_slope')
    weekly = _analyze_field('weekly_trend')
    monthly = _analyze_field('monthly_trend')

    # メイン指標（日足）を代表に
    main = daily['label']
    conf = daily['ratio']
    return {
        'main_label': main,
        'confidence': conf,
        'daily': daily,
        'weekly': weekly,
        'monthly': monthly,
    }