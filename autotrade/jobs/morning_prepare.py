"""
[FILE] autotrade/jobs/morning_prepare.py
[PATH] <project_root>/autotrade/jobs/morning_prepare.py

このファイルは何？
- 毎朝（cron）で動く「準備ジョブ」です。
- 役割：
  1) 銘柄候補（candidate）を読み込む
  2) 今日のピック（5〜10）を作る（今は暫定で先頭10）
  3) 20/60/120 のバックテストを “戦略ごと” に実行して保存する

初心者ポイント：
- まずは「動く土台」優先。銘柄選定は次フェーズで出来高/売買代金で強化します。
"""

from datetime import date
from django.conf import settings
from django.utils import timezone

from autotrade.models import AutoTradeDailyState
from autotrade.services.universe_service import build_daily_universe
from autotrade.services.backtest.aggregate import run_backtests_for_universe


def run():
    today = date.today()
    state, _ = AutoTradeDailyState.objects.get_or_create(date=today)

    # 1) 今日の銘柄（5〜10）を作る（現状：暫定ロジック）
    universe = build_daily_universe(limit=10)

    # 2) バックテスト（戦略別× 20/60/120）
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