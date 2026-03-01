"""
[FILE] autotrade/jobs/morning_prepare.py
[PATH] <project_root>/autotrade/jobs/morning_prepare.py

このファイルは何？
- 毎朝（cron）で動く「準備ジョブ」です。

今回の変更：
- rr_breakout を settings から渡さない（唯一の真実＝ACTIVE Snapshotの値）
- runner 側が snapshot から rr を確定する
"""

from datetime import date
from django.conf import settings
from django.utils import timezone

from autotrade.models import AutoTradeDailyState, AutoTradeSettingSnapshot
from autotrade.services.universe.service import build_daily_universe
from autotrade.services.common.guards import is_emergency_stopped
from autotrade.services.backtest.runner import run_detailed_backtests_for_universe

# ★ 自動チューニング（候補生成）
from autotrade.services.tuning.auto_tune import auto_tune_generate_candidate

# ★ 自動昇格
from autotrade.services.tuning.auto_promote import auto_promote_if_ready


def run():
    today = date.today()
    state, _ = AutoTradeDailyState.objects.get_or_create(date=today)

    # =========================================================
    # ★ 非常停止ガード
    # =========================================================
    if is_emergency_stopped(state):
        return

    # 1) 今日の銘柄（5〜10）
    universe = build_daily_universe(limit=10)
    picks = [x.get("ticker") for x in (universe.get("picks") or []) if x.get("ticker")]

    state.universe = universe
    state.updated_at = timezone.now()
    state.save()

    # 2) 本番採用中（ACTIVE）のSnapshotを使う（なければ停止扱い）
    snapshot = (
        AutoTradeSettingSnapshot.objects
        .filter(status="ACTIVE")
        .order_by("-created_at")
        .first()
    )

    if not snapshot or not picks:
        # snapshot未設定や銘柄なしなら、backtestは空のまま
        state.updated_at = timezone.now()
        state.save()
        return

    # 3) 詳細バックテスト実行（DB保存＋DailyState更新＋gate判定）
    # ★ rr_breakout は渡さない（runnerがsnapshotから確定）
    run_detailed_backtests_for_universe(
        snapshot=snapshot,
        picks=picks,
        target_date=today,
        windows=tuple(getattr(settings, "AUTOTRADE_BT_WINDOWS", [20, 60, 120])),
        rr_breakout=None,
        base_equity_yen=int(getattr(settings, "AUTOTRADE_BASE_EQUITY_YEN", 1_000_000)),
        force=True,
    )

    # 3.5) ★ 自動チューニング（最小構成）
    auto_tune_generate_candidate(
        target_date=today,
        windows=list(getattr(settings, "AUTOTRADE_BT_WINDOWS", [20, 60])),
    )

    # 4) ★ 自動昇格（OK不要・FULLのみ）
    auto_promote_if_ready(
        target_date=today,
        windows=list(getattr(settings, "AUTOTRADE_BT_WINDOWS", [20, 60])),
    )