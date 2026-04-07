"""
[FILE] autotrade/jobs/eod_stop_rescue.py
[PATH] <project_root>/autotrade/jobs/eod_stop_rescue.py

このファイルは何？
- 引け後に走る「STOP救済ジョブ」です。
- その日の gate が STOP の時だけ、自動チューニング→最良候補のACTIVE昇格を行います。
"""

from datetime import date

from autotrade.models import AutoTradeDailyState
from autotrade.services.common.guards import is_emergency_stopped
from autotrade.services.tuning.auto_promote import auto_promote_if_ready


def run():
    today = date.today()
    state, _ = AutoTradeDailyState.objects.get_or_create(date=today)

    if is_emergency_stopped(state):
        return {"ok": True, "skipped": True, "reason": "emergency_stop"}

    if str(state.gate_level or "STOP").upper().strip() != "STOP":
        return {
            "ok": True,
            "skipped": True,
            "reason": "state_not_stop",
            "gate_level": str(state.gate_level or ""),
        }

    return auto_promote_if_ready(target_date=today)