from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, Dict
from django.apps import apps

"""
市場レジームを TrendResult から算出するサービス。
- 日足:  daily_slope (float, >0=up, ==0=flat, <0=down)
- 週足:  weekly_trend (str 'up'/'flat'/'down' or numeric)
- 月足:  monthly_trend (str 'up'/'flat'/'down' or numeric)

返却:
{
  "headline": {"label":"上昇","pct":72.5},  # 日足ベースの見出し
  "daily":    {"label":"上昇","pct":72.5,"n":1234},
  "weekly":   {"label":"中立","pct":58.3,"n":1198},
  "monthly":  {"label":"下降","pct":34.0,"n":1177},
}
"""

@dataclass
class Ratio:
    label: str
    pct: float
    n: int

def _coerce_dir(val: Optional[object]) -> Optional[str]:
    """ 値を 'up'/'flat'/'down' に正規化 """
    if val is None:
        return None
    if isinstance(val, str):
        s = val.strip().lower()
        if s in ('up', 'flat', 'down'):
            return s
        return None
    if isinstance(val, (int, float)):
        if val > 0: return 'up'
        if val < 0: return 'down'
        return 'flat'
    return None

def _label_by_ratio(up_pct: float) -> str:
    if up_pct >= 70.0: return '上昇'
    if up_pct >= 40.0: return '中立'
    return '下降'

def _count_ratio_d() -> Ratio:
    TrendResult = apps.get_model('ai', 'TrendResult')
    qs = TrendResult.objects.only('daily_slope')
    total = 0; up = 0; flat = 0; down = 0
    for r in qs:
        d = _coerce_dir(getattr(r, 'daily_slope', None))
        if d is None:  # 判定不能は母数から除外
            continue
        total += 1
        if d == 'up': up += 1
        elif d == 'down': down += 1
        else: flat += 1
    if total == 0:
        return Ratio('データ不足', 0.0, 0)
    up_pct = round(up / total * 100.0, 1)
    return Ratio(_label_by_ratio(up_pct), up_pct, total)

def _count_ratio_w() -> Ratio:
    TrendResult = apps.get_model('ai', 'TrendResult')
    qs = TrendResult.objects.only('weekly_trend')
    total = 0; up = 0; flat = 0; down = 0
    for r in qs:
        d = _coerce_dir(getattr(r, 'weekly_trend', None))
        if d is None:  # 判定不能は母数から除外
            continue
        total += 1
        if d == 'up': up += 1
        elif d == 'down': down += 1
        else: flat += 1
    if total == 0:
        return Ratio('データ不足', 0.0, 0)
    up_pct = round(up / total * 100.0, 1)
    return Ratio(_label_by_ratio(up_pct), up_pct, total)

def _count_ratio_m() -> Ratio:
    TrendResult = apps.get_model('ai', 'TrendResult')
    qs = TrendResult.objects.only('monthly_trend')
    total = 0; up = 0; flat = 0; down = 0
    for r in qs:
        d = _coerce_dir(getattr(r, 'monthly_trend', None))
        if d is None:  # 判定不能は母数から除外
            continue
        total += 1
        if d == 'up': up += 1
        elif d == 'down': down += 1
        else: flat += 1
    if total == 0:
        return Ratio('データ不足', 0.0, 0)
    up_pct = round(up / total * 100.0, 1)
    return Ratio(_label_by_ratio(up_pct), up_pct, total)

def calculate_market_regime() -> Dict[str, Dict[str, object]]:
    """ 3階層（日/週/月）のレジームをまとめて返す """
    d = _count_ratio_d()
    w = _count_ratio_w()
    m = _count_ratio_m()
    # 見出しは日足ベース
    headline = {'label': d.label, 'confidence': d.pct}  # 後方互換（confidenceキー）
    return {
        'headline': {'label': d.label, 'pct': d.pct},
        'daily':    {'label': d.label, 'pct': d.pct, 'n': d.n},
        'weekly':   {'label': w.label, 'pct': w.pct, 'n': w.n},
        'monthly':  {'label': m.label, 'pct': m.pct, 'n': m.n},
        # 旧UI互換
        'label': headline['label'],
        'confidence': headline['confidence'],
    }