from __future__ import annotations
from typing import Optional, Dict
from django.apps import apps
from ai.infra.adapters.line import send_ai_flex

TrendResult = apps.get_model('ai', 'TrendResult')

def _coerce_dir(val: Optional[object]) -> Optional[str]:
    if val is None: return None
    if isinstance(val, str):
        s = val.strip().lower()
        if s in ('up', 'flat', 'down'): return s
        if s in ('1', '+', '↑'): return 'up'
        if s in ('-1', '-', '↓'): return 'down'
        return None
    if isinstance(val, (int, float)):
        if val > 0: return 'up'
        if val < 0: return 'down'
        return 'flat'
    return None

def _label(ratio: float) -> str:
    if ratio >= 70: return '上昇'
    if ratio >= 40: return '中立'
    return '下降'

def _calc_ratio(qs, field: str) -> Dict[str, float]:
    total = qs.count()
    if total == 0: return {'label': 'データ不足', 'ratio': 0.0}
    ups = flats = downs = 0
    for r in qs:
        d = _coerce_dir(getattr(r, field, None))
        if not d: continue
        if d == 'up': ups += 1
        elif d == 'down': downs += 1
        else: flats += 1
    valid = ups + downs + flats
    if valid == 0: return {'label': 'データ不足', 'ratio': 0.0}
    ratio = round(ups / valid * 100, 1)
    return {'label': _label(ratio), 'ratio': ratio}

def calculate_market_regime() -> Dict[str, Dict[str, float]]:
    qs = TrendResult.objects.all()
    return {
        'daily': _calc_ratio(qs, 'daily_slope'),
        'weekly': _calc_ratio(qs, 'weekly_trend'),
        'monthly': _calc_ratio(qs, 'monthly_trend'),
    }

def notify_regime_change(regime: dict, threshold: float = 10.0) -> None:
    """
    前回保存値と比べて±10%以上の変化があればLINE通知。
    """
    import json, os
    cache_path = "media/advisor/regime_last.json"
    last = {}
    if os.path.exists(cache_path):
        try:
            last = json.load(open(cache_path))
        except Exception:
            pass

    changed = []
    for k in ['daily', 'weekly', 'monthly']:
        prev = last.get(k, {}).get('ratio', 0)
        curr = regime[k]['ratio']
        diff = abs(curr - prev)
        if diff >= threshold:
            changed.append(f"{k}足：{prev:.1f}% → {curr:.1f}%（{regime[k]['label']}）")

    if changed:
        msg = ["📈 市況レジーム変化を検知", *changed]
        send_ai_flex("🧠 市況転換アラート", [{'name': 'AIレジーム検出', 'code': '-', 'sector': '-', 'score': 100, 'stars': 5, 'trend': {}, 'prices': {}}])
        print("LINE通知:", changed)

    # 現在値を保存
    os.makedirs("media/advisor", exist_ok=True)
    json.dump(regime, open(cache_path, "w"), ensure_ascii=False, indent=2)