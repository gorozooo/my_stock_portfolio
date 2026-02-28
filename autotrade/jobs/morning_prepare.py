"""
[FILE] autotrade/jobs/morning_prepare.py
[PATH] <project_root>/autotrade/jobs/morning_prepare.py

このファイルは何？
- 毎朝（cron）で動く「準備ジョブ」です。

役割：
  1) 今日のピック（5〜10）を作る
  2) 詳細バックテスト（BREAKOUT × 20/60/120）を実行して保存する（Execution基準で確定）
  3) 自動チューニングで CANDIDATE を作る（最小構成・1ノブだけ）
  4) 候補SnapshotがFULLなら自動でACTIVEへ昇格する（auto_promote）

重要な設計ルール：
- 非常停止（emergency_stop）が有効な日は、一切の状態更新を行わない
- gate判定は runner で確定。9:30 job は gate を再計算しない
- BREAKOUT一本運用：VWAP関連の引数（rr_vwap等）は一切渡さない
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
    run_detailed_backtests_for_universe(
        snapshot=snapshot,
        picks=picks,
        target_date=today,
        windows=tuple(getattr(settings, "AUTOTRADE_BT_WINDOWS", [20, 60, 120])),
        rr_breakout=float(getattr(settings, "AUTOTRADE_RR_BREAKOUT", 2.0)),
        base_equity_yen=int(getattr(settings, "AUTOTRADE_BASE_EQUITY_YEN", 1_000_000)),
        force=True,
    )

    # 3.5) ★ 自動チューニング（最小構成）
    # - ここで “候補(CANDIDATE)” を最大1つだけ作る
    # - 悪化は棄却（RETIRED）する
    auto_tune_generate_candidate(
        target_date=today,
        windows=list(getattr(settings, "AUTOTRADE_BT_WINDOWS", [20, 60])),
    )

    # 4) ★ 自動昇格（OK不要・FULLのみ）
    auto_promote_if_ready(
        target_date=today,
        windows=list(getattr(settings, "AUTOTRADE_BT_WINDOWS", [20, 60])),
    )