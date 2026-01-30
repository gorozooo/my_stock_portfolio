"""
[FILE] autotrade/jobs/end_of_day.py
[PATH] <project_root>/autotrade/jobs/end_of_day.py

このファイルは何？
- 15:10 に動く「日次クローズジョブ」です。
- 現段階では “状態更新の土台” だけ作っています。

初心者ポイント：
- ここは次フェーズで「約定/損益の取り込み（SBI/楽天）」を繋いで完成します。
"""

from datetime import date
from django.utils import timezone
from autotrade.models import AutoTradeDailyState


def run():
    today = date.today()
    state, _ = AutoTradeDailyState.objects.get_or_create(date=today)

    # TODO: ここで証券会社の損益を取り込み、pnl_day_yen / pnl_total_yen / equity_yen を更新する
    state.updated_at = timezone.now()
    state.save()