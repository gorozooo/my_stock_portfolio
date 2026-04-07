"""
[FILE] autotrade/jobs/morning_prepare.py
[PATH] <project_root>/autotrade/jobs/morning_prepare.py

このファイルは何？
- 毎朝（cron）で動く「準備ジョブ」です。

今回の変更：
- 朝は「ACTIVEで判定」しつつ、毎朝チューニングも必ず実行する
- ただし朝は自動昇格しない（市場前に本番設定を勝手に差し替えない）
- 引け後のSTOP救済昇格は別ジョブに分離する
"""

from datetime import date

from django.conf import settings
from django.utils import timezone

from autotrade.models import AutoTradeDailyState, AutoTradeSettingSnapshot
from autotrade.services.universe.service import build_daily_universe
from autotrade.services.common.guards import is_emergency_stopped
from autotrade.services.backtest.runner import run_detailed_backtests_for_universe
from autotrade.services.tuning.auto_tune import auto_tune_generate_candidate


def run():
    today = date.today()
    state, _ = AutoTradeDailyState.objects.get_or_create(date=today)

    # =========================================================
    # 0) 非常停止ガード
    # =========================================================
    if is_emergency_stopped(state):
        return {"ok": True, "skipped": True, "reason": "emergency_stop"}

    # =========================================================
    # 1) 今日の銘柄（5〜10）
    # =========================================================
    universe = build_daily_universe(limit=10)
    picks = [x.get("ticker") for x in (universe.get("picks") or []) if isinstance(x, dict) and x.get("ticker")]

    state.universe = universe
    state.updated_at = timezone.now()
    state.save(update_fields=["universe", "updated_at"])

    # =========================================================
    # 2) 本番採用中（ACTIVE）のSnapshotを使う
    # =========================================================
    snapshot = (
        AutoTradeSettingSnapshot.objects
        .filter(status="ACTIVE")
        .order_by("-created_at")
        .first()
    )

    if not snapshot or not picks:
        state.updated_at = timezone.now()
        state.save(update_fields=["updated_at"])
        return {
            "ok": True,
            "skipped": True,
            "reason": "no_active_or_no_picks",
            "has_active": bool(snapshot),
            "pick_count": len(picks),
        }

    # =========================================================
    # 3) ACTIVEで朝判定（唯一の真実）
    # =========================================================
    run_detailed_backtests_for_universe(
        snapshot=snapshot,
        picks=picks,
        target_date=today,
        windows=tuple(getattr(settings, "AUTOTRADE_BT_WINDOWS", [20, 40, 60])),
        rr_breakout=None,  # runner側が snapshot から確定
        base_equity_yen=int(getattr(settings, "AUTOTRADE_BASE_EQUITY_YEN", 1_000_000)),
        force=True,
    )

    # =========================================================
    # 4) 朝チューニング（毎朝必ず実行）
    #    - 候補は作る
    #    - ただし朝は昇格しない
    # =========================================================
    tune_res = auto_tune_generate_candidate(
        target_date=today,
        windows=list(getattr(settings, "AUTOTRADE_BT_WINDOWS", [20, 40, 60])),
        phase="MORNING",
    )

    return {
        "ok": True,
        "skipped": False,
        "reason": "morning_prepare_done",
        "active_snapshot_id": snapshot.id,
        "pick_count": len(picks),
        "tune": {
            "ok": bool(tune_res.ok),
            "skipped": bool(tune_res.skipped),
            "reason": str(tune_res.reason),
            "best_candidate_id": tune_res.best_candidate_id,
            "improved": bool(tune_res.improved),
        },
    }