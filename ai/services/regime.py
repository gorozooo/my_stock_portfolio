from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, Dict
from decimal import Decimal
from django.apps import apps

@dataclass
class Ratio:
    label: str
    pct: float
    n: int

def _coerce_dir(val: Optional[object]) -> Optional[str]:
    """
    数値(Decimal/float/int) → >0 up, ==0 flat, <0 down
    文字列 'up/flat/down' も許可
    """
    if val is None:
        return None
    if isinstance(val, str):
        s = val.strip().lower()
        if s in ('up','flat','down'):
            return s
        try:
            num = float(s)
        except Exception:
            return None
        if num > 0: return 'up'
        if num < 0: return 'down'
        return 'flat'
    if isinstance(val, (int, float, Decimal)):
        f = float(val)
        if f > 0: return 'up'
        if f < 0: return 'down'
        return 'flat'
    return None

def _label_by_ratio(up_pct: float) -> str:
    if up_pct >= 70.0: return '上昇'
    if up_pct >= 40.0: return '中立'
    return '下降'

def _count_ratio(field_name: str) -> Ratio:
    T = apps.get_model('ai','TrendResult')
    qs = T.objects.only(field_name)
    total = 0; up = 0; flat = 0; down = 0
    for r in qs:
        d = _coerce_dir(getattr(r, field_name, None))
        if d is None:
            continue
        total += 1
        if d == 'up': up += 1
        elif d == 'down': down += 1
        else: flat += 1
    if total == 0:
        return Ratio('データ不足', 0.0, 0)
    pct = round(up/total*100.0, 1)
    return Ratio(_label_by_ratio(pct), pct, total)

def calculate_market_regime() -> Dict[str, Dict[str, object]]:
    """
    優先順でレジーム見出しを決める：
      1) daily_slope が有効なレコードがあれば日足で
      2) なければ weekly_trend
      3) それもなければ monthly_trend
    さらに日/週/月の個別比率も返す。
    """
    d = _count_ratio('daily_slope')
    w = _count_ratio('weekly_trend')
    m = _count_ratio('monthly_trend')

    # 見出しフォールバック
    headline = d
    if d.n == 0:
        headline = w if w.n > 0 else m

    return {
        'headline': {'label': headline.label, 'pct': headline.pct},
        'daily':    {'label': d.label, 'pct': d.pct, 'n': d.n},
        'weekly':   {'label': w.label, 'pct': w.pct, 'n': w.n},
        'monthly':  {'label': m.label, 'pct': m.pct, 'n': m.n},
        # 旧UI互換キー
        'label': headline.label,
        'confidence': headline.pct,
    }