"""
[FILE] auto_promote.py
[PATH] <project_root>/autotrade/services/tuning/auto_promote.py

このファイルは何？
- 自動昇格の本体です。
- MORNING / EOD それぞれのフェーズで、候補Snapshotの中から
  「いまのACTIVEより良いもの」を ACTIVE に昇格させます。

今回の修正：
- 昇格判定に recent diagnosis（直近5営業日/10営業日の悪化診断）を直接反映
- RECENT_WEAK の時は、攻め方向の変更を昇格させない
- 5日/10日がかなり悪い時は、防御的な変更だけ昇格候補にする
- auto_tune が best と判定しても、auto_promote 側で最終ブレーキをかける
- MORNING は既存候補を使うだけ、EOD は必要なら再チューニング＋再判定する
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


def _iter_phase_candidates_for_date(*, target_date: dt_date, phase: str) -> List[AutoTradeSettingSnapshot]:
    """
    SQLite JSON lookup 依存を避けるため、Python側で安全に候補を拾う。
    """
    phase = _normalize_phase(phase)

    out: List[AutoTradeSettingSnapshot] = []
    qs = AutoTradeSettingSnapshot.objects.filter(status="CANDIDATE").order_by("-created_at", "-id")

    for x in qs:
        snap = x.snapshot if isinstance(x.snapshot, dict) else {}
        tune = snap.get("tune") if isinstance(snap.get("tune"), dict) else {}

        if str(tune.get("target_date") or "") != str(target_date):
            continue
        if _normalize_phase(tune.get("phase")) != phase:
            continue

        out.append(x)

    return out


def _extract_tune_eval(cand: AutoTradeSettingSnapshot) -> Dict[str, Any]:
    snap = cand.snapshot if isinstance(cand.snapshot, dict) else {}
    te = snap.get("tune_eval")
    return te if isinstance(te, dict) else {}


def _extract_candidate_pack(cand: AutoTradeSettingSnapshot) -> Dict[str, Any]:
    te = _extract_tune_eval(cand)
    cand_pack = te.get("cand") if isinstance(te.get("cand"), dict) else {}

    score = cand_pack.get("score") if isinstance(cand_pack.get("score"), dict) else {}
    params = cand_pack.get("params") if isinstance(cand_pack.get("params"), dict) else {}

    return {
        "gate_level": str(cand_pack.get("gate_level") or "STOP").upper().strip(),
        "score": {
            "pnl_sum_yen": _safe_int(score.get("pnl_sum_yen"), 0),
            "max_dd_pct": _safe_float(score.get("max_dd_pct"), 1.0),
            "pf_avg": _safe_float(score.get("pf_avg"), 0.0),
            "trades_sum": _safe_int(score.get("trades_sum"), 0),
        },
        "params": params,
        "knob": str(cand_pack.get("knob") or ""),
        "delta_label": str(cand_pack.get("delta_label") or ""),
        "source_snapshot_id": cand_pack.get("source_snapshot_id"),
    }


def _extract_base_pack(cand: AutoTradeSettingSnapshot) -> Dict[str, Any]:
    te = _extract_tune_eval(cand)
    base_pack = te.get("base") if isinstance(te.get("base"), dict) else {}

    score = base_pack.get("score") if isinstance(base_pack.get("score"), dict) else {}
    params = base_pack.get("params") if isinstance(base_pack.get("params"), dict) else {}

    return {
        "gate_level": str(base_pack.get("gate_level") or "STOP").upper().strip(),
        "score": {
            "pnl_sum_yen": _safe_int(score.get("pnl_sum_yen"), 0),
            "max_dd_pct": _safe_float(score.get("max_dd_pct"), 1.0),
            "pf_avg": _safe_float(score.get("pf_avg"), 0.0),
            "trades_sum": _safe_int(score.get("trades_sum"), 0),
        },
        "params": params,
        "active_snapshot_id": base_pack.get("active_snapshot_id"),
        "diagnosis_tags": list(base_pack.get("diagnosis_tags") or []),
        "regime_warning": str(base_pack.get("regime_warning") or ""),
    }


def _extract_candidate_diagnosis(cand: AutoTradeSettingSnapshot) -> Dict[str, Any]:
    snap = cand.snapshot if isinstance(cand.snapshot, dict) else {}
    diagnosis = snap.get("diagnosis")
    return diagnosis if isinstance(diagnosis, dict) else {}


def _candidate_improved(cand: AutoTradeSettingSnapshot) -> bool:
    snap = cand.snapshot if isinstance(cand.snapshot, dict) else {}
    te = _extract_tune_eval(cand)

    if bool(te.get("improved_vs_active")):
        return True
    if bool(snap.get("tune_improved")):
        return True
    return False


def _candidate_selected(cand: AutoTradeSettingSnapshot) -> bool:
    snap = cand.snapshot if isinstance(snap := cand.snapshot, dict) else {}
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


def _move_tag_from_pack(pack: Dict[str, Any]) -> Tuple[str, str]:
    knob = str(pack.get("knob") or "").strip()
    delta_label = str(pack.get("delta_label") or "").strip()

    if knob in ["rr_breakout", "stop_pct", "lookback_bars", "max_hold_bars", "sma_days"]:
        if delta_label.startswith("+"):
            return knob, "UP"
        if delta_label.startswith("-"):
            return knob, "DOWN"
        return knob, ""

    if knob == "daily_filter":
        if "→SMA" in delta_label or "->SMA" in delta_label:
            return knob, "TO_SMA"
        if "→OFF" in delta_label or "->OFF" in delta_label:
            return knob, "TO_OFF"
        return knob, ""

    if knob == "direction":
        if "→TREND_ONLY" in delta_label or "->TREND_ONLY" in delta_label:
            return knob, "TO_TREND_ONLY"
        if "→BOTH" in delta_label or "->BOTH" in delta_label:
            return knob, "TO_BOTH"
        return knob, ""

    return knob, ""


def _defensive_move_set() -> set[Tuple[str, str]]:
    return {
        ("lookback_bars", "UP"),
        ("max_hold_bars", "UP"),
        ("sma_days", "UP"),
        ("stop_pct", "UP"),
        ("daily_filter", "TO_SMA"),
        ("direction", "TO_TREND_ONLY"),
        ("rr_breakout", "DOWN"),
    }


def _aggressive_move_set() -> set[Tuple[str, str]]:
    return {
        ("lookback_bars", "DOWN"),
        ("max_hold_bars", "DOWN"),
        ("sma_days", "DOWN"),
        ("stop_pct", "DOWN"),
        ("daily_filter", "TO_OFF"),
        ("direction", "TO_BOTH"),
        ("rr_breakout", "UP"),
    }


def _recent_guard_for_candidate(
    *,
    cand: AutoTradeSettingSnapshot,
    diagnosis: Dict[str, Any],
    phase: str,
) -> Dict[str, Any]:
    """
    直近弱い時の昇格ブレーキ。

    ルール:
    - RECENT_WEAK で攻め方向の変更は昇格禁止
    - 5日/10日がかなり悪い時は、防御的な変更だけ許可
    - diagnosis の preferred_moves に一致する候補は優先
    """
    phase = _normalize_phase(phase)
    diagnosis = diagnosis if isinstance(diagnosis, dict) else {}

    pack = _extract_candidate_pack(cand)
    knob, move = _move_tag_from_pack(pack)

    tags = [str(x) for x in (diagnosis.get("diagnosis_tags") or []) if str(x).strip()]
    regime_warning = str(diagnosis.get("regime_warning") or "").upper().strip()

    windows = diagnosis.get("windows") if isinstance(diagnosis.get("windows"), dict) else {}
    w5 = windows.get("5") if isinstance(windows.get("5"), dict) else {}
    w10 = windows.get("10") if isinstance(windows.get("10"), dict) else {}

    pf5 = _safe_float(w5.get("pf"), 0.0)
    pf10 = _safe_float(w10.get("pf"), 0.0)
    pnl5 = _safe_int(w5.get("pnl_sum_yen"), 0)
    pnl10 = _safe_int(w10.get("pnl_sum_yen"), 0)

    preferred_moves = diagnosis.get("preferred_moves") if isinstance(diagnosis.get("preferred_moves"), list) else []
    preferred_map: Dict[Tuple[str, str], int] = {}
    for x in preferred_moves:
        if not isinstance(x, dict):
            continue
        k = str(x.get("knob") or "").strip()
        p = str(x.get("prefer") or "").strip()
        w = _safe_int(x.get("weight"), 0)
        if k and p:
            preferred_map[(k, p)] = w

    exact_preferred = (knob, move) in preferred_map
    preferred_weight = _safe_int(preferred_map.get((knob, move)), 0)

    defensive = (knob, move) in _defensive_move_set()
    aggressive = (knob, move) in _aggressive_move_set()

    weak_tags = {"RECENT_BREAKDOWN", "NO_EDGE", "LOSING_STREAKY"}
    weak = (regime_warning == "RECENT_WEAK") or bool(set(tags) & weak_tags)

    very_weak_pf5 = _safe_float(getattr(settings, "AUTOTRADE_PROMOTE_GUARD_PF5_STRICT", 0.35), 0.35)
    very_weak_pf10 = _safe_float(getattr(settings, "AUTOTRADE_PROMOTE_GUARD_PF10_STRICT", 0.80), 0.80)
    very_weak_loss5 = _safe_int(getattr(settings, "AUTOTRADE_PROMOTE_GUARD_LOSS5_STRICT", -3000), -3000)
    very_weak_loss10 = _safe_int(getattr(settings, "AUTOTRADE_PROMOTE_GUARD_LOSS10_STRICT", -5000), -5000)

    very_weak = (
        (pf5 < very_weak_pf5 and pf10 < very_weak_pf10)
        or (pnl5 <= very_weak_loss5 and pnl10 <= very_weak_loss10)
    )

    allow = True
    reasons: List[str] = []

    if weak and aggressive:
        allow = False
        reasons.append("直近が弱いので、攻め方向の変更は昇格禁止")

    if very_weak and not (defensive or exact_preferred):
        allow = False
        reasons.append("5日/10日がかなり悪いので、防御的な変更だけ許可")

    if phase == "MORNING" and pf5 < _safe_float(getattr(settings, "AUTOTRADE_PROMOTE_GUARD_MORNING_PF5", 0.50), 0.50):
        if aggressive:
            allow = False
            reasons.append("朝は5日PFが弱すぎるため、攻め方向の朝昇格は禁止")

    if phase == "MORNING" and pnl5 < _safe_int(getattr(settings, "AUTOTRADE_PROMOTE_GUARD_MORNING_LOSS5", -2500), -2500):
        if aggressive:
            allow = False
            reasons.append("朝は直近5日損失が大きいため、攻め方向の朝昇格は禁止")

    alignment_score = 0
    if exact_preferred:
        alignment_score = max(2, preferred_weight)
    elif defensive:
        alignment_score = 1
    elif aggressive:
        alignment_score = -2
    else:
        alignment_score = 0

    return {
        "allow": bool(allow),
        "alignment_score": int(alignment_score),
        "exact_preferred": bool(exact_preferred),
        "defensive": bool(defensive),
        "aggressive": bool(aggressive),
        "regime_warning": regime_warning,
        "tags": tags,
        "reasons": reasons,
        "pf5": float(pf5),
        "pf10": float(pf10),
        "pnl5": int(pnl5),
        "pnl10": int(pnl10),
        "knob": knob,
        "move": move,
    }


def _rank_tuple_from_candidate(
    cand: AutoTradeSettingSnapshot,
    *,
    diagnosis: Dict[str, Any],
    phase: str,
) -> Tuple[int, int, int, int, int, int, int, int]:
    pack = _extract_candidate_pack(cand)
    guard = _recent_guard_for_candidate(cand=cand, diagnosis=diagnosis, phase=phase)

    selected_score = 1 if _candidate_selected(cand) else 0
    improved_score = 1 if _candidate_improved(cand) else 0
    allow_score = 1 if guard.get("allow") else 0
    align_score = _safe_int(guard.get("alignment_score"), 0)
    core = _rank_tuple_from_pack(pack)

    return (
        allow_score,
        improved_score,
        selected_score,
        align_score,
        core[0],
        core[1],
        core[2],
        core[3],
    )


def _pick_best_existing_candidate(
    *,
    target_date: dt_date,
    phase: str,
    diagnosis: Dict[str, Any],
) -> Tuple[Optional[AutoTradeSettingSnapshot], List[int], Dict[str, Any]]:
    candidates = list(_iter_phase_candidates_for_date(target_date=target_date, phase=phase))
    retained_ids = [int(x.id) for x in candidates]

    if not candidates:
        return None, retained_ids, {}

    allowed_improved: List[AutoTradeSettingSnapshot] = []
    best_guard: Dict[str, Any] = {}

    for cand in candidates:
        if not _candidate_improved(cand):
            continue

        guard = _recent_guard_for_candidate(cand=cand, diagnosis=diagnosis, phase=phase)
        if not guard.get("allow"):
            continue

        allowed_improved.append(cand)

    if not allowed_improved:
        return None, retained_ids, {}

    best = max(
        allowed_improved,
        key=lambda x: _rank_tuple_from_candidate(x, diagnosis=diagnosis, phase=phase),
    )
    best_guard = _recent_guard_for_candidate(cand=best, diagnosis=diagnosis, phase=phase)
    return best, retained_ids, best_guard


def _promotion_note(phase: str) -> str:
    return "auto_promote_morning" if phase == "MORNING" else "auto_promote_eod"


def _promotion_reason(phase: str) -> str:
    return "promoted_best_candidate_morning" if phase == "MORNING" else "promoted_best_candidate_eod"


def _refresh_active_baseline(
    *,
    state: AutoTradeDailyState,
    active: Optional[AutoTradeSettingSnapshot],
    target_date: dt_date,
    windows: Optional[List[int]],
    phase: str,
) -> Dict[str, Any]:
    """
    EOD前に、現ACTIVEの基準値を当日Universeで再計算し直す。
    """
    phase = _normalize_phase(phase)

    if phase != "EOD":
        return {
            "ok": True,
            "skipped": True,
            "reason": "refresh_not_needed",
        }

    if active is None:
        return {
            "ok": True,
            "skipped": True,
            "reason": "no_active_to_refresh",
        }

    uni = state.universe if isinstance(state.universe, dict) else {}
    picks = [x.get("ticker") for x in (uni.get("picks") or []) if isinstance(x, dict) and x.get("ticker")]
    picks = [str(x).strip() for x in (picks or []) if str(x).strip()]

    if not picks:
        return {
            "ok": True,
            "skipped": True,
            "reason": "no_picks_for_refresh",
            "active_id": active.id,
        }

    run_detailed_backtests_for_universe(
        snapshot=active,
        picks=picks,
        target_date=target_date,
        windows=tuple(int(x) for x in (windows or getattr(settings, "AUTOTRADE_BT_WINDOWS", [20, 40, 60]))),
        rr_breakout=None,
        base_equity_yen=None,
        force=True,
    )

    return {
        "ok": True,
        "skipped": False,
        "reason": "baseline_refreshed",
        "active_id": active.id,
    }


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

    refresh_res = _refresh_active_baseline(
        state=state,
        active=active,
        target_date=target_date,
        windows=windows,
        phase=phase,
    )

    tune_payload: Dict[str, Any] = {}
    retained_candidate_ids: List[int] = []
    best_candidate_id: Optional[int] = None
    improved = False
    diagnosis: Dict[str, Any] = {}

    if run_tune:
        tune_res = auto_tune_generate_candidate(
            target_date=target_date,
            windows=windows,
            phase=phase,
        )

        diagnosis = getattr(tune_res, "diagnosis", {}) if isinstance(getattr(tune_res, "diagnosis", {}), dict) else {}

        tune_payload = {
            "best_candidate_id": getattr(tune_res, "best_candidate_id", None),
            "improved": bool(getattr(tune_res, "improved", False)),
            "base": getattr(tune_res, "base", {}),
            "best": getattr(tune_res, "best", {}),
            "diagnosis": diagnosis,
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
                "refresh": refresh_res,
                "tune": tune_payload,
            }

    # run_tune の有無に関係なく、phase候補を recent diagnosis ブレーキ込みで選び直す
    if not diagnosis:
        candidates_probe = _iter_phase_candidates_for_date(target_date=target_date, phase=phase)
        if candidates_probe:
            diagnosis = _extract_candidate_diagnosis(candidates_probe[0])

    best_cand, existing_retained_ids, best_guard = _pick_best_existing_candidate(
        target_date=target_date,
        phase=phase,
        diagnosis=diagnosis,
    )

    if existing_retained_ids:
        retained_candidate_ids = existing_retained_ids

    if best_cand is None:
        return {
            "ok": True,
            "promoted": False,
            "reason": "no_promotable_candidate",
            "phase": phase,
            "candidate_id": None,
            "improved": False,
            "retained_candidate_ids": retained_candidate_ids,
            "refresh": refresh_res,
            "tune": tune_payload,
            "diagnosis": {
                "regime_warning": str(diagnosis.get("regime_warning") or ""),
                "diagnosis_tags": list(diagnosis.get("diagnosis_tags") or []),
                "preferred_knobs": list(diagnosis.get("preferred_knobs") or []),
            },
        }

    best_candidate_id = int(best_cand.id)
    improved = _candidate_improved(best_cand)

    if not improved:
        return {
            "ok": True,
            "promoted": False,
            "reason": "best_candidate_not_better_than_active",
            "phase": phase,
            "candidate_id": best_candidate_id,
            "old_active_id": (active.id if active else None),
            "improved": False,
            "retained_candidate_ids": retained_candidate_ids,
            "refresh": refresh_res,
            "tune": tune_payload,
            "guard": best_guard,
            "diagnosis": {
                "regime_warning": str(diagnosis.get("regime_warning") or ""),
                "diagnosis_tags": list(diagnosis.get("diagnosis_tags") or []),
                "preferred_knobs": list(diagnosis.get("preferred_knobs") or []),
            },
        }

    cand = best_cand
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
        "recent_guard": best_guard,
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
        "guard": best_guard,
        "base": tune_payload.get("base") or {},
        "best": tune_payload.get("best") or _extract_candidate_pack(cand),
        "diagnosis": diagnosis,
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
        "refresh": refresh_res,
        "guard": best_guard,
        "tune": tune_payload,
        "diagnosis": {
            "regime_warning": str(diagnosis.get("regime_warning") or ""),
            "diagnosis_tags": list(diagnosis.get("diagnosis_tags") or []),
            "preferred_knobs": list(diagnosis.get("preferred_knobs") or []),
        },
    }