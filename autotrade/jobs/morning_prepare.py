"""
[FILE] autotrade/jobs/morning_prepare.py
[PATH] <project_root>/autotrade/jobs/morning_prepare.py

このファイルは何？
- 毎朝（cron）で動く「準備ジョブ」です。

今回の変更：
- 朝はまず ACTIVE を基準に判定用バックテストを回す
- その上で毎朝必ずチューニングする
- 現ACTIVEより良い候補があれば、朝の時点で自動昇格する
- 改善余地がありそうな候補は複数CANDIDATEとして残す
- 最後に「いまのACTIVE」で朝判定をもう一度固定し直す
"""

from datetime import date

from django.conf import settings
from django.utils import timezone

from autotrade.models import AutoTradeDailyState, AutoTradeSettingSnapshot
from autotrade.services.universe.service import build_daily_universe
from autotrade.services.common.guards import is_emergency_stopped
from autotrade.services.backtest.runner import run_detailed_backtests_for_universe
from autotrade.services.tuning.auto_tune import auto_tune_generate_candidate
from autotrade.services.tuning.auto_promote import auto_promote_if_ready


def _get_active_snapshot():
    return (
        AutoTradeSettingSnapshot.objects
        .filter(status="ACTIVE")
        .order_by("-created_at")
        .first()
    )


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
    # 2) 現ACTIVE取得
    # =========================================================
    active_before = _get_active_snapshot()
    if not active_before or not picks:
        state.updated_at = timezone.now()
        state.save(update_fields=["updated_at"])
        return {
            "ok": True,
            "skipped": True,
            "reason": "no_active_or_no_picks",
            "has_active": bool(active_before),
            "pick_count": len(picks),
        }

    # =========================================================
    # 3) まず「現ACTIVE」で朝判定用バックテストを固定
    #    ※ auto_tune は state.backtest を基準に比較するため、ここが土台
    # =========================================================
    run_detailed_backtests_for_universe(
        snapshot=active_before,
        picks=picks,
        target_date=today,
        windows=tuple(getattr(settings, "AUTOTRADE_BT_WINDOWS", [20, 40, 60])),
        rr_breakout=None,  # runner側が snapshot から確定
        base_equity_yen=int(getattr(settings, "AUTOTRADE_BASE_EQUITY_YEN", 1_000_000)),
        force=True,
    )

    # =========================================================
    # 4) 朝チューニング（毎朝必ず）
    #    - 複数ノブを試す
    #    - 良さそうなものは複数CANDIDATEで残す
    #    - ここではまだ昇格しない
    # =========================================================
    tune_res = auto_tune_generate_candidate(
        target_date=today,
        windows=list(getattr(settings, "AUTOTRADE_BT_WINDOWS", [20, 40, 60])),
        phase="MORNING",
    )

    # =========================================================
    # 5) 朝の自動昇格
    #    - 現ACTIVEより良い候補があれば、この時点で昇格
    #    - 残りの有望候補は CANDIDATE のまま残す
    # =========================================================
    promote_res = auto_promote_if_ready(
        target_date=today,
        windows=list(getattr(settings, "AUTOTRADE_BT_WINDOWS", [20, 40, 60])),
        phase="MORNING",
        run_tune=False,      # 直前にもう tune 済み
        stop_only=False,     # 朝は STOP 限定ではない
    )

    # =========================================================
    # 6) 朝の最終ACTIVEを取り直して、最終判定を固定し直す
    #    ※ ここで 8:55/9:30 側が参照する state.backtest / gate が
    #       「昇格後のACTIVE」に揃う
    # =========================================================
    active_after = _get_active_snapshot()
    if active_after and picks:
        run_detailed_backtests_for_universe(
            snapshot=active_after,
            picks=picks,
            target_date=today,
            windows=tuple(getattr(settings, "AUTOTRADE_BT_WINDOWS", [20, 40, 60])),
            rr_breakout=None,
            base_equity_yen=int(getattr(settings, "AUTOTRADE_BASE_EQUITY_YEN", 1_000_000)),
            force=True,
        )

    return {
        "ok": True,
        "skipped": False,
        "reason": "morning_prepare_done",
        "active_before_id": (active_before.id if active_before else None),
        "active_after_id": (active_after.id if active_after else None),
        "pick_count": len(picks),
        "tune": {
            "ok": bool(tune_res.ok),
            "skipped": bool(tune_res.skipped),
            "reason": str(tune_res.reason),
            "phase": str(tune_res.phase),
            "best_candidate_id": tune_res.best_candidate_id,
            "retained_candidate_ids": list(tune_res.retained_candidate_ids or []),
            "improved": bool(tune_res.improved),
        },
        "promote": promote_res,
    }