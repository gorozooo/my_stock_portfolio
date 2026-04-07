"""
[FILE] autotrade/services/tuning/auto_promote.py
[PATH] <project_root>/autotrade/services/tuning/auto_promote.py

このファイルは何？
- 自動昇格サービスです。
- 朝は「良い候補があれば即昇格」
- 引け後は「朝CANDIDATEも再チューニングして、ACTIVEより良ければ昇格」
- 使わない候補は RETIRED に寄せます
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


def _get_active_snapshot() -> Optional[AutoTradeSettingSnapshot]:
    return (
        AutoTradeSettingSnapshot.objects
        .filter(status="ACTIVE")
        .order_by("-created_at")
        .first()
    )


def _iter_candidates_for_date(*, target_date: dt_date) -> List[AutoTradeSettingSnapshot]:
    out: List[AutoTradeSettingSnapshot] = []
    for x in AutoTradeSettingSnapshot.objects.filter(status="CANDIDATE").order_by("-id"):
        snap = x.snapshot if isinstance(x.snapshot, dict) else {}
        tune = snap.get("tune") if isinstance(snap.get("tune"), dict) else {}
        if str(tune.get("target_date") or "") == str(target_date):
            out.append(x)
    return out


def _iter_candidates_for_phase(*, target_date: dt_date, phase: str) -> List[AutoTradeSettingSnapshot]:
    phase = str(phase or "").upper().strip()
    out: List[AutoTradeSettingSnapshot] = []
    for x in _iter_candidates_for_date(target_date=target_date):
        snap = x.snapshot if isinstance(x.snapshot, dict) else {}
        tune = snap.get("tune") if isinstance(snap.get("tune"), dict) else {}
        if str(tune.get("phase") or "").upper().strip() == phase:
            out.append(x)
    return out


def _retire_snapshot(snap: AutoTradeSettingSnapshot, *, note: str) -> None:
    raw = snap.snapshot if isinstance(snap.snapshot, dict) else {}
    raw["retire"] = {
        "retired_at": timezone.localtime(timezone.now()).isoformat(),
        "note": str(note or ""),
    }
    snap.snapshot = raw
    snap.status = "RETIRED"
    snap.save(update_fields=["status", "snapshot"])


def _retire_other_candidates(*, target_date: dt_date, keep_ids: List[int], note: str) -> None:
    keep = set(int(x) for x in (keep_ids or []))
    for x in _iter_candidates_for_date(target_date=target_date):
        if int(x.id) in keep:
            continue
        _retire_snapshot(x, note=note)


@transaction.atomic
def auto_promote_if_ready(
    *,
    target_date: Optional[dt_date] = None,
    windows: Optional[List[int]] = None,
    phase: str = "MORNING",
    run_tune: bool = True,
    stop_only: bool = False,
) -> Dict[str, Any]:
    if target_date is None:
        target_date = timezone.localdate()

    phase = str(phase or "MORNING").upper().strip()
    if phase not in ["MORNING", "EOD"]:
        phase = "MORNING"

    state, _ = AutoTradeDailyState.objects.get_or_create(date=target_date)

    if is_emergency_stopped(state):
        return {"ok": True, "skipped": True, "reason": "emergency_stop"}

    if stop_only and str(state.gate_level or "STOP").upper().strip() != "STOP":
        return {
            "ok": True,
            "skipped": True,
            "reason": "state_not_stop",
            "gate_level": str(state.gate_level or ""),
        }

    old_active = _get_active_snapshot()

    tune_res = None
    if run_tune:
        tune_res = auto_tune_generate_candidate(
            target_date=target_date,
            windows=windows,
            phase=phase,
        )

        if not tune_res.ok:
            return {
                "ok": False,
                "skipped": True,
                "reason": str(tune_res.reason),
                "phase": phase,
                "tune": {
                    "best_candidate_id": tune_res.best_candidate_id,
                    "improved": tune_res.improved,
                    "retained_candidate_ids": list(tune_res.retained_candidate_ids or []),
                },
            }

    # 昇格候補の取り方
    if phase == "MORNING":
        # 朝は MORNING で残した候補のうち、best_candidate_id があれば昇格対象
        candidate_id = tune_res.best_candidate_id if tune_res is not None else None
        retained_ids = list(tune_res.retained_candidate_ids or []) if tune_res is not None else []
        improved = bool(tune_res.improved) if tune_res is not None else False
    else:
        # 引け後は EOD で残した最良1件のみ対象
        candidate_id = tune_res.best_candidate_id if tune_res is not None else None
        retained_ids = list(tune_res.retained_candidate_ids or []) if tune_res is not None else []
        improved = bool(tune_res.improved) if tune_res is not None else False

    if candidate_id is None:
        # 引け後は、最終的に使わない候補を残さない
        if phase == "EOD":
            _retire_other_candidates(
                target_date=target_date,
                keep_ids=[],
                note="eod_finished_without_promotion",
            )
        return {
            "ok": True,
            "promoted": False,
            "reason": "no_promotable_candidate",
            "phase": phase,
            "candidate_id": None,
            "improved": improved,
            "retained_candidate_ids": retained_ids,
        }

    cand = (
        AutoTradeSettingSnapshot.objects
        .filter(id=candidate_id)
        .first()
    )
    if cand is None:
        if phase == "EOD":
            _retire_other_candidates(
                target_date=target_date,
                keep_ids=[],
                note="eod_candidate_missing",
            )
        return {
            "ok": False,
            "promoted": False,
            "reason": "best_candidate_missing",
            "phase": phase,
            "candidate_id": candidate_id,
        }

    # 改善していないなら昇格しない
    if not improved:
        if phase == "EOD":
            _retire_other_candidates(
                target_date=target_date,
                keep_ids=[],
                note="eod_best_not_better_than_active",
            )
        return {
            "ok": True,
            "promoted": False,
            "reason": "best_candidate_not_better_than_active",
            "phase": phase,
            "candidate_id": cand.id,
            "old_active_id": (old_active.id if old_active else None),
            "retained_candidate_ids": retained_ids,
        }

    # 昇格
    if old_active and old_active.id != cand.id:
        old_snap = old_active.snapshot if isinstance(old_active.snapshot, dict) else {}
        old_snap["retire"] = {
            "retired_at": timezone.localtime(timezone.now()).isoformat(),
            "retired_by": f"AUTO_{phase}",
            "date": str(target_date),
            "note": f"{phase.lower()}_promoted_better_candidate",
        }
        old_active.snapshot = old_snap
        old_active.status = "RETIRED"
        old_active.save(update_fields=["status", "snapshot"])

    cand_snap = cand.snapshot if isinstance(cand.snapshot, dict) else {}
    cand_snap["promote"] = {
        "promoted_at": timezone.localtime(timezone.now()).isoformat(),
        "promoted_by": f"AUTO_{phase}",
        "date": str(target_date),
        "from_status": "CANDIDATE",
        "old_active_id": (old_active.id if old_active else None),
        "note": f"promote_best_candidate_{phase.lower()}",
    }
    cand.snapshot = cand_snap
    cand.status = "ACTIVE"
    cand.save(update_fields=["status", "snapshot"])

    AutoTradePromotionLog.objects.create(
        user=cand.user,
        action="PROMOTE",
        from_snapshot=old_active,
        to_snapshot=cand,
        note=f"auto_promote_{phase.lower()}",
    )

    # 朝は、残りの retained 候補は CANDIDATE で残す
    # 引け後は、昇格した1件以外は RETIRED に寄せる
    if phase == "EOD":
        _retire_other_candidates(
            target_date=target_date,
            keep_ids=[cand.id],
            note="eod_finished_promoted_other_candidates_retired",
        )

    rules = state.rules if isinstance(state.rules, dict) else {}
    rules_key = "morning_promotion" if phase == "MORNING" else "eod_promotion"
    rules[rules_key] = {
        "promoted_at": timezone.localtime(timezone.now()).isoformat(),
        "old_active_id": (old_active.id if old_active else None),
        "new_active_id": cand.id,
        "candidate_id": cand.id,
        "phase": phase,
        "retained_candidate_ids": retained_ids,
        "reason": f"best_candidate_promoted_{phase.lower()}",
        "base": (tune_res.base if tune_res else {}),
        "best": (tune_res.best if tune_res else {}),
    }
    state.rules = rules

    base_reason = (state.gate_reason or "").rstrip()
    add_reason = f"【自動昇格:{phase}】ACTIVEを {old_active.id if old_active else '-'} → {cand.id} に更新"
    if add_reason not in base_reason:
        state.gate_reason = (base_reason + "\n" + add_reason).strip() if base_reason else add_reason

    state.updated_at = timezone.now()
    state.save(update_fields=["rules", "gate_reason", "updated_at"])

    return {
        "ok": True,
        "promoted": True,
        "reason": f"promoted_best_candidate_{phase.lower()}",
        "phase": phase,
        "old_active_id": (old_active.id if old_active else None),
        "new_active_id": cand.id,
        "candidate_id": cand.id,
        "retained_candidate_ids": retained_ids,
        "tune": {
            "best_candidate_id": (tune_res.best_candidate_id if tune_res else cand.id),
            "improved": bool(tune_res.improved if tune_res else True),
            "base": (tune_res.base if tune_res else {}),
            "best": (tune_res.best if tune_res else {}),
        },
    }