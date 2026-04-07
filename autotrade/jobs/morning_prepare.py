"""
[FILE] morning_prepare.py
[PATH] <project_root>/autotrade/jobs/morning_prepare.py

このファイルは何？
- 毎朝（cron）で動く「準備ジョブ」です。
- その日の対象銘柄を作り、ACTIVEで朝判定し、さらに朝チューニング→朝昇格判定まで行います。

今回の修正：
- 朝は必ずチューニングする
- その直後、MORNINGで作ったCANDIDATE群を使って朝昇格判定する
- 朝昇格判定では再チューニングしない（直前に作った候補をそのまま使う）
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
    # 2) 朝の基準ACTIVEを取得
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

    active_before_id = snapshot.id

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
    # =========================================================
    tune_res = auto_tune_generate_candidate(
        target_date=today,
        windows=list(getattr(settings, "AUTOTRADE_BT_WINDOWS", [20, 40, 60])),
        phase="MORNING",
    )

    # =========================================================
    # 5) 朝昇格判定
    #    - ここでは再チューニングしない
    #    - 直前の MORNING 候補をそのまま拾って昇格判定する
    # =========================================================
    promote_res = auto_promote_if_ready(
        target_date=today,
        windows=list(getattr(settings, "AUTOTRADE_BT_WINDOWS", [20, 40, 60])),
        phase="MORNING",
        run_tune=False,
    )

    active_after = (
        AutoTradeSettingSnapshot.objects
        .filter(status="ACTIVE")
        .order_by("-created_at")
        .first()
    )

    return {
        "ok": True,
        "skipped": False,
        "reason": "morning_prepare_done",
        "active_before_id": active_before_id,
        "active_after_id": (active_after.id if active_after else None),
        "pick_count": len(picks),
        "tune": {
            "ok": bool(getattr(tune_res, "ok", False)),
            "skipped": bool(getattr(tune_res, "skipped", True)),
            "reason": str(getattr(tune_res, "reason", "")),
            "phase": str(getattr(tune_res, "phase", "MORNING")),
            "best_candidate_id": getattr(tune_res, "best_candidate_id", None),
            "retained_candidate_ids": list(getattr(tune_res, "retained_candidate_ids", []) or []),
            "improved": bool(getattr(tune_res, "improved", False)),
        },
        "promote": promote_res,
    }