"""
[FILE] autotrade/services/tuning/auto_promote.py
[PATH] <project_root>/autotrade/services/tuning/auto_promote.py

このファイルは何？
- 引け後の STOP 救済昇格を行うサービスです。
- その日の STOP ACTIVE より良い候補を作れたら、旧ACTIVEをRETIREDにして昇格させます。

今回の変更：
- FULL候補だけ昇格、をやめる
- 「旧ACTIVEより改善している最良候補」を昇格対象にする
- 候補生成は auto_tune_generate_candidate(EOD_STOP) に委譲する
"""

from __future__ import annotations

from datetime import date as dt_date
from typing import Any, Dict, List, Optional

from django.db import transaction
from django.utils import timezone

from autotrade.models import (
    AutoTradeDailyState,
    AutoTradePromotionLog,
    AutoTradeSettingSnapshot,
)
from autotrade.services.common.guards import is_emergency_stopped
from autotrade.services.tuning.auto_tune import auto_tune_generate_candidate


@transaction.atomic
def auto_promote_if_ready(
    *,
    target_date: Optional[dt_date] = None,
    windows: Optional[List[int]] = None,
) -> Dict[str, Any]:
    if target_date is None:
        target_date = timezone.localdate()

    state, _ = AutoTradeDailyState.objects.get_or_create(date=target_date)

    if is_emergency_stopped(state):
        return {"ok": True, "skipped": True, "reason": "emergency_stop"}

    # 引け後救済は「STOPの日だけ」
    if str(state.gate_level or "STOP").upper().strip() != "STOP":
        return {
            "ok": True,
            "skipped": True,
            "reason": "state_not_stop",
            "gate_level": str(state.gate_level or ""),
        }

    active = (
        AutoTradeSettingSnapshot.objects
        .filter(status="ACTIVE")
        .order_by("-created_at")
        .first()
    )

    tune_res = auto_tune_generate_candidate(
        target_date=target_date,
        windows=windows,
        phase="EOD_STOP",
    )

    if not tune_res.ok:
        return {
            "ok": False,
            "skipped": True,
            "reason": str(tune_res.reason),
            "tune": {
                "best_candidate_id": tune_res.best_candidate_id,
                "improved": tune_res.improved,
            },
        }

    if tune_res.best_candidate_id is None:
        return {
            "ok": True,
            "promoted": False,
            "reason": str(tune_res.reason),
            "candidate_id": None,
            "improved": bool(tune_res.improved),
        }

    cand = (
        AutoTradeSettingSnapshot.objects
        .filter(id=tune_res.best_candidate_id)
        .first()
    )
    if cand is None:
        return {
            "ok": False,
            "promoted": False,
            "reason": "best_candidate_missing",
            "candidate_id": tune_res.best_candidate_id,
        }

    # 改善していない候補は昇格しない
    if not tune_res.improved:
        return {
            "ok": True,
            "promoted": False,
            "reason": "best_candidate_not_better_than_active",
            "candidate_id": cand.id,
            "old_active_id": (active.id if active else None),
        }

    old_active = active

    if old_active and old_active.id != cand.id:
        old_snap = old_active.snapshot if isinstance(old_active.snapshot, dict) else {}
        old_snap["retire"] = {
            "retired_at": timezone.localtime(timezone.now()).isoformat(),
            "retired_by": "AUTO_STOP_RESCUE",
            "date": str(target_date),
            "note": "stop_day_replaced_by_better_candidate",
        }
        old_active.snapshot = old_snap
        old_active.status = "RETIRED"
        old_active.save(update_fields=["status", "snapshot"])

    cand_snap = cand.snapshot if isinstance(cand.snapshot, dict) else {}
    cand_snap["promote"] = {
        "promoted_at": timezone.localtime(timezone.now()).isoformat(),
        "promoted_by": "AUTO_STOP_RESCUE",
        "date": str(target_date),
        "from_status": "CANDIDATE",
        "old_active_id": (old_active.id if old_active else None),
        "note": "promote_best_candidate_after_stop",
    }
    cand.snapshot = cand_snap
    cand.status = "ACTIVE"
    cand.save(update_fields=["status", "snapshot"])

    AutoTradePromotionLog.objects.create(
        user=cand.user,
        action="PROMOTE",
        from_snapshot=old_active,
        to_snapshot=cand,
        note="auto_promote_after_stop",
    )

    rules = state.rules if isinstance(state.rules, dict) else {}
    rules["stop_rescue"] = {
        "rescued_at": timezone.localtime(timezone.now()).isoformat(),
        "old_active_id": (old_active.id if old_active else None),
        "new_active_id": cand.id,
        "candidate_id": cand.id,
        "reason": "stop_day_best_candidate_promoted",
        "base": tune_res.base,
        "best": tune_res.best,
    }
    state.rules = rules

    base_reason = (state.gate_reason or "").rstrip()
    add_reason = f"【引け後救済】ACTIVEを {old_active.id if old_active else '-'} → {cand.id} に更新"
    if add_reason not in base_reason:
        state.gate_reason = (base_reason + "\n" + add_reason).strip() if base_reason else add_reason

    state.updated_at = timezone.now()
    state.save(update_fields=["rules", "gate_reason", "updated_at"])

    return {
        "ok": True,
        "promoted": True,
        "reason": "promoted_best_candidate_after_stop",
        "old_active_id": (old_active.id if old_active else None),
        "new_active_id": cand.id,
        "candidate_id": cand.id,
        "tune": {
            "best_candidate_id": tune_res.best_candidate_id,
            "improved": bool(tune_res.improved),
            "base": tune_res.base,
            "best": tune_res.best,
        },
    }