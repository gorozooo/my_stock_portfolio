"""
[FILE] eod_stop_rescue.py
[PATH] <project_root>/autotrade/jobs/eod_stop_rescue.py

このファイルは何？
- 引け後に走る「EOD 自動見直しジョブ」です。
- 毎日、ACTIVE + 朝CANDIDATE を再チューニングして、より良い候補があれば昇格します。

今回の修正：
- 直近5営業日 / 10営業日の recent diagnosis を rules に保存します
- STOP日だけでなく毎日EOD見直しを行います
"""

from datetime import date

from django.utils import timezone

from autotrade.models import AutoTradeDailyState
from autotrade.services.common.guards import is_emergency_stopped
from autotrade.services.tuning.auto_promote import auto_promote_if_ready
from autotrade.services.tuning.recent_diagnosis import build_recent_diagnosis


def run():
    today = date.today()
    state, _ = AutoTradeDailyState.objects.get_or_create(date=today)

    if is_emergency_stopped(state):
        return {"ok": True, "skipped": True, "reason": "emergency_stop"}

    eod_diagnosis = build_recent_diagnosis(
        target_date=today,
        phase="EOD",
        mode="PAPER",
        strategy="BREAKOUT",
    )

    rules = state.rules if isinstance(state.rules, dict) else {}
    diag_root = rules.get("recent_diagnosis") if isinstance(rules.get("recent_diagnosis"), dict) else {}
    diag_root["EOD"] = eod_diagnosis
    rules["recent_diagnosis"] = diag_root
    state.rules = rules
    state.updated_at = timezone.now()
    state.save(update_fields=["rules", "updated_at"])

    res = auto_promote_if_ready(
        target_date=today,
        phase="EOD",
        run_tune=True,
    )

    res["diagnosis"] = {
        "regime_warning": eod_diagnosis.get("regime_warning"),
        "diagnosis_tags": list(eod_diagnosis.get("diagnosis_tags") or []),
        "preferred_knobs": list(eod_diagnosis.get("preferred_knobs") or []),
    }
    return res