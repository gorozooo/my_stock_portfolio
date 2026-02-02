"""
[FILE] autotrade/jobs/end_of_day.py
[PATH] <project_root>/autotrade/jobs/end_of_day.py

このファイルは何？
- 15:10 に動く「日次クローズジョブ」です。

役割：
- その日の状態をクローズし、日次情報を確定させる
- 現段階では「状態更新の土台」だけを持つ

重要な設計ルール：
- 非常停止（emergency_stop）が有効な日は、状態を書き換えない
- 停止中は「凍結」が正解

初心者ポイント：
- 次フェーズで証券会社APIと接続し、損益・残高を取り込みます
"""

from datetime import date
from django.utils import timezone

from autotrade.models import AutoTradeDailyState
from autotrade.services.common.guards import is_emergency_stopped


def run():
    today = date.today()
    state, _ = AutoTradeDailyState.objects.get_or_create(date=today)

    # =========================================================
    # ★ 非常停止ガード
    # =========================================================
    if is_emergency_stopped(state):
        return

    # TODO:
    # - 証券会社APIから損益を取得
    # - pnl_day_yen / pnl_total_yen / equity_yen を更新

    state.updated_at = timezone.now()
    state.save()