"""
[FILE] autotrade/jobs/morning_prepare.py
[PATH] <project_root>/autotrade/jobs/morning_prepare.py

このファイルは何？
- 毎朝（cron）で動く「準備ジョブ」です。

役割：
  1) 銘柄候補（candidate）を読み込む
  2) 今日のピック（5〜10）を作る（現状は暫定で先頭10）
  3) 20/60/120 のバックテストを「戦略ごと」に実行して保存する

重要な設計ルール：
- 非常停止（emergency_stop）が有効な日は、一切の状態更新を行わない
- 止める責務は job に集約し、services は純粋関数として保つ

初心者ポイント：
- まずは「毎日同じ流れで状態が作られる」ことを優先
- 銘柄選定ロジック（出来高・ボラ等）は次フェーズで強化します
"""

from datetime import date
from django.conf import settings
from django.utils import timezone

from autotrade.models import AutoTradeDailyState
from autotrade.services.universe.service import build_daily_universe
from autotrade.services.backtest.aggregate import run_backtests_for_universe
from autotrade.services.common.guards import is_emergency_stopped


def run():
    today = date.today()
    state, _ = AutoTradeDailyState.objects.get_or_create(date=today)

    # =========================================================
    # ★ 非常停止ガード
    # =========================================================
    if is_emergency_stopped(state):
        # 非常停止中は universe / backtest を一切更新しない
        return

    # 1) 今日の銘柄（5〜10）を作る（現状：暫定ロジック）
    universe = build_daily_universe(limit=10)

    # 2) バックテスト（戦略別 × 20/60/120）
    bt = run_backtests_for_universe(
        picks=[x["ticker"] for x in universe.get("picks", [])],
        windows=settings.AUTOTRADE_BT_WINDOWS,
        rr_breakout=settings.AUTOTRADE_RR_BREAKOUT,
        rr_vwap=settings.AUTOTRADE_RR_VWAP,
    )

    state.universe = universe
    state.backtest = bt
    state.updated_at = timezone.now()
    state.save()