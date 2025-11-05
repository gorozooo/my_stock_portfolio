from __future__ import annotations
from typing import Optional
from django.apps import apps
from django.utils.timezone import now
from ai.infra.adapters.line import send_ops_alert
import json, os

SNAP_PATH = "media/advisor/regime_last.json"

def _coerce_dir(val: Optional[object]) -> Optional[str]:
    if val is None: return None
    if isinstance(val, str):
        s = val.strip().lower()
        if s in ('up', 'flat', 'down'): return s
        return None
    if isinstance(val, (int, float)):
        if val > 0: return 'up'
        elif val < 0: return 'down'
        return 'flat'
    return None

def _calc_ratio(field: str) -> float:
    T = apps.get_model('ai','TrendResult')
    qs = T.objects.all()
    total = up = 0
    for r in qs:
        val = getattr(r, field, None)
        d = _coerce_dir(val)
        if not d: continue
        total += 1
        if d == 'up': up += 1
    if total == 0:
        return 0.0
    return round(up / total * 100.0, 1)

def _label(ratio: float) -> str:
    if ratio >= 70: return '上昇'
    if ratio >= 40: return '中立'
    return '下降'

def calculate_market_regime() -> dict:
    daily = _calc_ratio('daily_slope')
    weekly = _calc_ratio('weekly_trend')
    monthly = _calc_ratio('monthly_trend')

    data = {
        'date': now().strftime('%Y-%m-%d %H:%M'),
        'daily': {'ratio': daily, 'label': _label(daily)},
        'weekly': {'ratio': weekly, 'label': _label(weekly)},
        'monthly': {'ratio': monthly, 'label': _label(monthly)},
    }

    _check_and_notify_change(data)
    return data

def _check_and_notify_change(data: dict):
    """前回との差を比較し、大きく変化したらLINE通知"""
    if not os.path.exists(SNAP_PATH):
        os.makedirs(os.path.dirname(SNAP_PATH), exist_ok=True)
        with open(SNAP_PATH, "w") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return

    try:
        with open(SNAP_PATH, "r") as f:
            prev = json.load(f)
    except Exception:
        prev = {}

    changes = []
    for key in ("daily", "weekly", "monthly"):
        old = prev.get(key, {}).get("label")
        new = data.get(key, {}).get("label")
        if old and new and old != new:
            changes.append(f"{key.upper()}：{old} → {new}")

    if changes:
        send_ops_alert("📊 レジーム変化通知", changes)

    with open(SNAP_PATH, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)