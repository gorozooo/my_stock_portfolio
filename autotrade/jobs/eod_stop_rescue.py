"""
[FILE] autotrade/jobs/eod_stop_rescue.py
[PATH] <project_root>/autotrade/jobs/eod_stop_rescue.py

このファイルは何？
- 引け後に走る「EOD 自動見直しジョブ」です。
- 旧ファイル名は stop_rescue ですが、今は STOP 日限定ではなく毎日動かします。

今回の変更：
- 毎日、朝に残した CANDIDATE も含めて再チューニングする
- その日の ACTIVE より良い候補が見つかれば引け後に昇格する
- STOP の日は探索幅を強めて救済寄りに探す
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

    return auto_promote_if_ready(
        target_date=today,
        phase="EOD",
        run_tune=True,      # 引け後は候補も再チューニング
        stop_only=False,    # STOP日限定ではなく毎日見る
    )