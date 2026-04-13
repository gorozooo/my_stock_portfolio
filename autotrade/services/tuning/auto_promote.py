"""
[FILE] autotrade/services/tuning/auto_promote.py
[PATH] <project_root>/autotrade/services/tuning/auto_promote.py

このファイルは何？
- 自動昇格の本体です。
- MORNING / EOD それぞれのフェーズで、候補Snapshotの中から
  「いまのACTIVEより良いもの」を ACTIVE に昇格させます。

今回の修正：
- MORNING は「tune_selected=True の1件」を最優先で昇格判定する
- EOD は昇格判定の前に、現ACTIVEで baseline を再計算する
- これで「tune.best_candidate_id」と「実際に昇格したACTIVE」がズレる事故を防ぐ
- これで EOD が古いACTIVE基準で比較する事故も防ぐ
"""

from __future__ import annotations

from datetime import date as dt_date
from typing import Any, Dict, List, Optional, Tuple

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from autotrade.models import (
    AutoTradeDailyState,
    AutoTradePromotionLog,
    AutoTradeSettingSnapshot,
)
from autotrade.services.common.guards import is_emergency_stopped
from autotrade.services.backtest.runner import run_detailed_backtests_for_universe
from autotrade.services.tuning.auto_tune import auto_tune_generate_candidate


_GATE_RANK = {"STOP": 0, "LIGHT": 1, "FULL": 2}


def _safe_int(x: Any, default: int = 0) -> int:
    try:
        return int(x)
    except Exception:
        return int(default)


def _safe_float(x: Any, default: float = 0.0) -> float:
    try:
        return float(x)
    except Exception:
        return float(default)


def _normalize_phase(phase: Optional[str]) -> str:
    p = str(phase or "EOD").upper().strip()
    if p in ["EOD_STOP", "EOD"]:
        return "EOD"
    if p == "MORNING":
        return "MORNING"
    return "EOD"


def _candidate_queryset(*, target_date: dt_date, phase: str):
    return (
        AutoTradeSettingSnapshot.objects
        .filter(
            status="CANDIDATE",
            snapshot__tune__target_date=str(target_date),
            snapshot__tune__phase=str(phase),
        )
        .order_by("-created_at", "-id")
    )


def _extract_tune_eval(cand: AutoTradeSettingSnapshot) -> Dict[str, Any]:
    snap = cand.snapshot if isinstance(cand.snapshot, dict) else {}
    te = snap.get("tune_eval") if isinstance(te.get("tune_eval"), dict) else {}
    return te


def _extract_candidate_pack(cand: AutoTradeSettingSnapshot) -> Dict[str, Any]:
    te = _extract_tune_eval(cand)
    cand_pack = te.get("cand") if isinstance(te.get("cand"), dict) else {}

    score = cand_pack.get("score") if isinstance(cand_pack.get("score"), dict) else {}
    return {
        "gate_level": str(cand_pack.get("gate_level") or "STOP").upper().strip(),
        "score": {
            "pnl_sum_yen": _safe_int(score.get("pnl_sum_yen"), 0),
            "max_dd_pct": _safe_float(score.get("max_dd_pct"), 1.0),
            "pf_avg": _safe_float(score.get("pf_avg"), 0.0),
            "trades_sum": _safe_int(score.get("trades_sum"), 0),
        },
        "params": cand_pack.get("params") if isinstance(cand_pack.get("params"), dict) else {},
        "knob": str(cand_pack.get("knob") or ""),
        "delta_label": str(cand_pack.get("delta_label") or ""),
        "source_snapshot_id": cand_pack.get("source_snapshot_id"),
    }


def _extract_base_pack(cand: AutoTradeSettingSnapshot) -> Dict[str, Any]:
    te = _extract_tune_eval(cand)
    base_pack = te.get("base") if isinstance(te.get("base"), dict) else {}

    score = base_pack.get("score") if isinstance(base_pack.get("score"), dict) else {}
    return {
        "gate_level": str(base_pack.get("gate_level") or "STOP").upper().strip(),
        "score": {
            "pnl_sum_yen": _safe_int(score.get("pnl_sum_yen"), 0),
            "max_dd_pct": _safe_float(score.get("max_dd_pct"), 1.0),
            "pf_avg": _safe_float(score.get("pf_avg"), 0.0),
            "trades_sum": _safe_int(score.get("trades_sum"), 0),
        },
        "params": base_pack.get("params") if isinstance(base_pack.get("params"), dict) else {},
        "active_snapshot_id": base_pack.get("active_snapshot_id"),
    }


def _candidate_improved(cand: AutoTradeSettingSnapshot) -> bool:
    snap = cand.snapshot if isinstance(cand.snapshot, dict) else {}
    te = _extract_tune_eval(cand)

    if bool(te.get("improved_vs_active")):
        return True
    if bool(snap.get("tune_improved")):
        return True
    return False


def _candidate_selected(cand: AutoTradeSettingSnapshot) -> bool:
    snap = cand.snapshot if isinstance(cand.snapshot, dict) else {}
    return bool(snap.get("tune_selected"))


def _rank_tuple_from_pack(pack: Dict[str, Any]) -> Tuple[int, int, int, int, int]:
    score = pack.get("score") if isinstance(pack.get("score"), dict) else {}

    gate_level = str(pack.get("gate_level") or "STOP").upper().strip()
    pf_score = int(round(_safe_float(score.get("pf_avg"), 0.0) * 1000.0))
    pnl_score = _safe_int(score.get("pnl_sum_yen"), 0)
    dd_score = -int(round(_safe_float(score.get("max_dd_pct"), 1.0) * 100000.0))
    trade_score = _safe_int(score.get("trades_sum"), 0)

    return (
        _GATE_RANK.get(gate_level, 0),
        pf_score,
        pnl_score,
        dd_score,
        trade_score,
    )


def _rank_tuple_from_candidate(cand: AutoTradeSettingSnapshot) -> Tuple[int, int, int, int, int, int, int]:
    pack = _extract_candidate_pack(cand)
    selected_score = 1 if _candidate_selected(cand) else 0
    improved_score = 1 if _candidate_improved(cand) else 0
    core = _rank_tuple_from_pack(pack)
    return (
        improved_score,
        selected_score,
        core[0],
        core[1],
        core[2],
        core[3],
        core[4],
    )


def _pick_best_existing_candidate(
    *,
    target_date: dt_date,
    phase: str,
) -> Tuple[Optional[AutoTradeSettingSnapshot], List[int]]:
    candidates = list(_candidate_queryset(target_date=target_date, phase=phase))
    retained_ids = [int(x.id) for x in candidates]

    if not candidates:
        return None, retained_ids

    selected_improved = [x for x in candidates if _candidate_selected(x) and _candidate_improved(x)]
    if selected_improved:
        return max(selected_improved, key=_rank_tuple_from_candidate), retained_ids

    selected_only = [x for x in candidates if _candidate_selected(x)]
    if selected_only:
        return max(selected_only, key=_rank_tuple_from_candidate), retained_ids

    improved_only = [x for x in candidates if _candidate_improved(x)]
    if improved_only:
        return max(improved_only, key=_rank_tuple_from_candidate), retained_ids

    return None, retained_ids


def _promotion_note(phase: str) -> str:
    return "auto_promote_morning" if phase == "MORNING" else "auto_promote_eod"


def _promotion_reason(phase: str) -> str:
    return "promoted_best_candidate_morning" if phase == "MORNING" else "promoted_best_candidate_eod"


def _refresh_state_baseline_with_active(
    *,
    state: AutoTradeDailyState,
    active: Optional[AutoTradeSettingSnapshot],
    target_date: dt_date,
    windows: Optional[List[int]],
) -> Dict[str, Any]:
    """
    現ACTIVEを基準に state.backtest を再計算する。
    EOD前にこれをやることで、古いACTIVE基準のまま比較する事故を防ぐ。
    """
    if active is None:
        return {"ok": False, "skipped": True, "reason": "no_active_snapshot"}

    uni = state.universe if isinstance(state.universe, dict) else {}
    picks = [x.get("ticker") for x in (uni.get("picks") or []) if isinstance(x, dict) and x.get("ticker")]
    picks = [str(x).strip() for x in (picks or []) if str(x).strip()]

    if not picks:
        return {"ok": True, "skipped": True, "reason": "no_picks"}

    run_detailed_backtests_for_universe(
        snapshot=active,
        picks=picks,
        target_date=target_date,
        windows=tuple(int(x) for x in (windows or getattr(settings, "AUTOTRADE_BT_WINDOWS", [20, 40, 60]))),
        rr_breakout=None,
        base_equity_yen=int(getattr(settings, "AUTOTRADE_BASE_EQUITY_YEN", 1_000_000)),
        force=True,
    )
    return {"ok": True, "skipped": False, "reason": "baseline_refreshed", "active_id": active.id}


@transaction.atomic
def auto_promote_if_ready(
    *,
    target_date: Optional[dt_date] = None,
    windows: Optional[List[int]] = None,
    phase: str = "EOD",
    run_tune: bool = True,
) -> Dict[str, Any]:
    if target_date is None:
        target_date = timezone.localdate()

    phase = _normalize_phase(phase)

    state, _ = AutoTradeDailyState.objects.get_or_create(date=target_date)

    if is_emergency_stopped(state):
        return {
            "ok": True,
            "skipped": True,
            "reason": "emergency_stop",
            "phase": phase,
        }

    active = (
        AutoTradeSettingSnapshot.objects
        .filter(status="ACTIVE")
        .order_by("-created_at")
        .first()
    )

    # EOD はまず「今のACTIVE」で baseline を揃える
    refresh_payload: Dict[str, Any] = {}
    if phase == "EOD" and active is not None:
        refresh_payload = _refresh_state_baseline_with_active(
            state=state,
            active=active,
            target_date=target_date,
            windows=windows,
        )

    tune_payload: Dict[str, Any] = {}
    retained_candidate_ids: List[int] = []
    best_candidate_id: Optional[int] = None
    improved = False

    if run_tune:
        tune_res = auto_tune_generate_candidate(
            target_date=target_date,
            windows=windows,
            phase=phase,
        )

        tune_payload = {
            "best_candidate_id": getattr(tune_res, "best_candidate_id", None),
            "improved": bool(getattr(tune_res, "improved", False)),
            "base": getattr(tune_res, "base", {}),
            "best": getattr(tune_res, "best", {}),
            "diagnosis": getattr(tune_res, "diagnosis", {}),
        }
        retained_candidate_ids = list(getattr(tune_res, "retained_candidate_ids", []) or [])
        best_candidate_id = getattr(tune_res, "best_candidate_id", None)
        improved = bool(getattr(tune_res, "improved", False))

        if not bool(getattr(tune_res, "ok", False)):
            return {
                "ok": False,
                "promoted": False,
                "reason": str(getattr(tune_res, "reason", "tune_failed")),
                "phase": phase,
                "candidate_id": best_candidate_id,
                "improved": improved,
                "retained_candidate_ids": retained_candidate_ids,
                "refresh": refresh_payload,
                "tune": tune_payload,
            }
    else:
        best_cand, retained_candidate_ids = _pick_best_existing_candidate(
            target_date=target_date,
            phase=phase,
        )

        if best_cand is None:
            return {
                "ok": True,
                "promoted": False,
                "reason": "no_promotable_candidate",
                "phase": phase,
                "candidate_id": None,
                "improved": False,
                "retained_candidate_ids": retained_candidate_ids,
                "refresh": refresh_payload,
                "tune": {
                    "best_candidate_id": None,
                    "improved": False,
                    "base": {},
                    "best": {},
                },
            }

        best_candidate_id = int(best_cand.id)
        improved = _candidate_improved(best_cand)
        tune_payload = {
            "best_candidate_id": best_candidate_id,
            "improved": improved,
            "base": _extract_base_pack(best_cand),
            "best": _extract_candidate_pack(best_cand),
        }

    if best_candidate_id is None:
        return {
            "ok": True,
            "promoted": False,
            "reason": "no_promotable_candidate",
            "phase": phase,
            "candidate_id": None,
            "improved": improved,
            "retained_candidate_ids": retained_candidate_ids,
            "refresh": refresh_payload,
            "tune": tune_payload,
        }

    cand = (
        AutoTradeSettingSnapshot.objects
        .filter(id=best_candidate_id)
        .first()
    )
    if cand is None:
        return {
            "ok": False,
            "promoted": False,
            "reason": "best_candidate_missing",
            "phase": phase,
            "candidate_id": best_candidate_id,
            "improved": improved,
            "retained_candidate_ids": retained_candidate_ids,
            "refresh": refresh_payload,
            "tune": tune_payload,
        }

    if not improved:
        return {
            "ok": True,
            "promoted": False,
            "reason": "best_candidate_not_better_than_active",
            "phase": phase,
            "candidate_id": cand.id,
            "old_active_id": (active.id if active else None),
            "improved": False,
            "retained_candidate_ids": retained_candidate_ids,
            "refresh": refresh_payload,
            "tune": tune_payload,
        }

    old_active = active

    if old_active and old_active.id != cand.id:
        old_snap = old_active.snapshot if isinstance(old_active.snapshot, dict) else {}
        old_snap["retire"] = {
            "retired_at": timezone.localtime(timezone.now()).isoformat(),
            "retired_by": f"AUTO_{phase}",
            "date": str(target_date),
            "note": f"{phase.lower()}_replaced_by_better_candidate",
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
        "note": _promotion_note(phase),
    }
    cand.snapshot = cand_snap
    cand.status = "ACTIVE"
    cand.save(update_fields=["status", "snapshot"])

    AutoTradePromotionLog.objects.create(
        user=cand.user,
        action="PROMOTE",
        from_snapshot=old_active,
        to_snapshot=cand,
        note=_promotion_note(phase),
    )

    rules = state.rules if isinstance(state.rules, dict) else {}
    rules_key = "morning_promote" if phase == "MORNING" else "eod_promote"
    rules[rules_key] = {
        "promoted_at": timezone.localtime(timezone.now()).isoformat(),
        "old_active_id": (old_active.id if old_active else None),
        "new_active_id": cand.id,
        "candidate_id": cand.id,
        "reason": _promotion_reason(phase),
        "phase": phase,
        "retained_candidate_ids": retained_candidate_ids,
        "base": tune_payload.get("base") or {},
        "best": tune_payload.get("best") or {},
        "refresh": refresh_payload,
    }
    state.rules = rules

    add_reason = f"【{phase}昇格】ACTIVEを {old_active.id if old_active else '-'} → {cand.id} に更新"
    base_reason = (state.gate_reason or "").rstrip()
    if add_reason not in base_reason:
        state.gate_reason = (base_reason + "\n" + add_reason).strip() if base_reason else add_reason

    state.updated_at = timezone.now()
    state.save(update_fields=["rules", "gate_reason", "updated_at"])

    return {
        "ok": True,
        "promoted": True,
        "reason": _promotion_reason(phase),
        "phase": phase,
        "old_active_id": (old_active.id if old_active else None),
        "new_active_id": cand.id,
        "candidate_id": cand.id,
        "improved": True,
        "retained_candidate_ids": retained_candidate_ids,
        "refresh": refresh_payload,
        "tune": tune_payload,
    }