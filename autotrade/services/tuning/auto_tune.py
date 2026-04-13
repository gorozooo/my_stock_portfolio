"""
[FILE] auto_tune.py
[PATH] <project_root>/autotrade/services/tuning/auto_tune.py

このファイルは何？
- 自動チューニングの本体です。
- ACTIVE を基準に複数ノブを一気に試し、その日の候補を作ります。

今回の変更：
- 直近5営業日 / 10営業日の実Execution診断を取り込みます
- 「全部試す」は維持しつつ、最近の壊れ方に合う候補へ優先度ボーナスを付けます
- 朝(MORNING)は良い候補を複数 CANDIDATE に残します
- 引け後(EOD)は ACTIVE + 朝CANDIDATE を再チューニングし、不要候補は整理します
- 候補評価中に DailyState を壊さないよう、state を退避→復元します
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import date as dt_date
from typing import Any, Dict, List, Optional, Tuple

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from autotrade.models import AutoTradeDailyState, AutoTradeSettingSnapshot
from autotrade.services.common.guards import is_emergency_stopped
from autotrade.services.backtest.runner import run_detailed_backtests_for_universe
from autotrade.services.tuning.recent_diagnosis import build_recent_diagnosis


_GATE_RANK = {"STOP": 0, "LIGHT": 1, "FULL": 2}


@dataclass(frozen=True)
class AutoTuneResult:
    ok: bool
    skipped: bool
    reason: str
    phase: str
    best_candidate_id: Optional[int]
    improved: bool
    retained_candidate_ids: List[int] = field(default_factory=list)
    created_candidate_ids: List[int] = field(default_factory=list)
    base: Dict[str, Any] = field(default_factory=dict)
    best: Dict[str, Any] = field(default_factory=dict)
    diagnosis: Dict[str, Any] = field(default_factory=dict)


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


def _get_active_snapshot() -> Optional[AutoTradeSettingSnapshot]:
    return (
        AutoTradeSettingSnapshot.objects
        .filter(status="ACTIVE")
        .order_by("-created_at")
        .first()
    )


def _iter_candidate_snapshots_for_date(*, target_date: dt_date) -> List[AutoTradeSettingSnapshot]:
    out: List[AutoTradeSettingSnapshot] = []
    for x in AutoTradeSettingSnapshot.objects.filter(status="CANDIDATE").order_by("-id"):
        snap = x.snapshot if isinstance(x.snapshot, dict) else {}
        tune = snap.get("tune") if isinstance(snap.get("tune"), dict) else {}
        if str(tune.get("target_date") or "") == str(target_date):
            out.append(x)
    return out


def _iter_phase_generated_candidates(*, target_date: dt_date, phase: str) -> List[AutoTradeSettingSnapshot]:
    phase = str(phase or "").upper().strip()
    out: List[AutoTradeSettingSnapshot] = []
    for x in AutoTradeSettingSnapshot.objects.filter(status="CANDIDATE").order_by("-id"):
        snap = x.snapshot if isinstance(x.snapshot, dict) else {}
        tune = snap.get("tune") if isinstance(snap.get("tune"), dict) else {}
        if str(tune.get("target_date") or "") != str(target_date):
            continue
        if str(tune.get("phase") or "").upper().strip() != phase:
            continue
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


def _clear_phase_generated_candidates(*, target_date: dt_date, phase: str) -> None:
    for x in _iter_phase_generated_candidates(target_date=target_date, phase=phase):
        _retire_snapshot(x, note=f"clear_previous_{phase.lower()}_generated")


def _get_gate_bundle_from_state(state: AutoTradeDailyState) -> Dict[str, Any]:
    bt = state.backtest if isinstance(state.backtest, dict) else {}
    gate = bt.get("gate") if isinstance(bt.get("gate"), dict) else {}
    final = gate.get("final") if isinstance(gate.get("final"), dict) else {}

    final_level = str(final.get("gate_level") or state.gate_level or "STOP").upper().strip()

    breakout = gate.get("BREAKOUT") if isinstance(gate.get("BREAKOUT"), dict) else {}
    lv_b = str(breakout.get("gate_level") or final_level or "STOP").upper().strip()

    if lv_b == "STOP":
        active = []
        disabled = ["BREAKOUT"]
    else:
        active = ["BREAKOUT"]
        disabled = []

    return {
        "final_level": lv_b,
        "active": active,
        "disabled": disabled,
        "breakout": breakout,
    }


def _get_rr_from_snapshot(snapshot: AutoTradeSettingSnapshot) -> float:
    snap = snapshot.snapshot if isinstance(snapshot.snapshot, dict) else {}
    lab = snap.get("lab") if isinstance(snap.get("lab"), dict) else {}
    br = lab.get("BREAKOUT") if isinstance(lab.get("BREAKOUT"), dict) else {}

    rr_b = _safe_float(br.get("rr"), 0.0)
    if rr_b > 0:
        return rr_b

    rr_b = _safe_float(snap.get("rr_breakout"), 0.0)
    if rr_b > 0:
        return rr_b

    tune = snap.get("tune") if isinstance(snap.get("tune"), dict) else {}
    rr_b = _safe_float(tune.get("rr_breakout"), 0.0)
    if rr_b > 0:
        return rr_b

    return _safe_float(getattr(settings, "AUTOTRADE_RR_BREAKOUT", 2.0), 2.0)


def _get_baseline_metrics_from_state(state: AutoTradeDailyState, *, windows: List[int]) -> Dict[int, Dict[str, Dict[str, Any]]]:
    bt = state.backtest if isinstance(state.backtest, dict) else {}
    by_window = bt.get("by_window") if isinstance(bt.get("by_window"), dict) else {}

    out: Dict[int, Dict[str, Dict[str, Any]]] = {}

    for w in windows:
        w_int = int(w)
        w_key_s = str(w_int)

        if isinstance(by_window.get(w_int), dict):
            w_dict = by_window.get(w_int)
        elif isinstance(by_window.get(w_key_s), dict):
            w_dict = by_window.get(w_key_s)
        else:
            w_dict = {}

        out[w_int] = {
            "BREAKOUT": w_dict.get("BREAKOUT") if isinstance(w_dict.get("BREAKOUT"), dict) else {},
        }

    return out


def _aggregate_score(metrics_by_window: Dict[int, Dict[str, Dict[str, Any]]], *, windows: List[int]) -> Dict[str, Any]:
    pnl = 0
    dd_pct = 0.0
    pf = 0.0
    trades = 0

    for w in windows:
        m = ((metrics_by_window.get(int(w)) or {}).get("BREAKOUT") or {})
        pnl += _safe_int(m.get("total_pnl"), 0)
        dd_pct = max(dd_pct, _safe_float(m.get("max_drawdown_pct"), 0.0))
        pf += _safe_float(m.get("profit_factor"), 0.0)
        trades += _safe_int(m.get("trades"), 0)

    pf_avg = pf / max(1, len(windows))

    return {
        "pnl_sum_yen": int(pnl),
        "max_dd_pct": float(dd_pct),
        "pf_avg": float(pf_avg),
        "trades_sum": int(trades),
    }


def _build_tune_eval(base: Dict[str, Any], cand: Dict[str, Any], diagnosis: Dict[str, Any]) -> Dict[str, Any]:
    b_gate = str(base.get("gate_level") or "STOP")
    c_gate = str(cand.get("gate_level") or "STOP")

    b = base.get("score") or {}
    c = cand.get("score") or {}

    b_pnl = _safe_int(b.get("pnl_sum_yen"), 0)
    c_pnl = _safe_int(c.get("pnl_sum_yen"), 0)

    b_dd = _safe_float(b.get("max_dd_pct"), 0.0)
    c_dd = _safe_float(c.get("max_dd_pct"), 0.0)

    b_pf = _safe_float(b.get("pf_avg"), 0.0)
    c_pf = _safe_float(c.get("pf_avg"), 0.0)

    b_tr = _safe_int(b.get("trades_sum"), 0)
    c_tr = _safe_int(c.get("trades_sum"), 0)

    reasons: List[str] = []

    if _GATE_RANK.get(c_gate, 0) > _GATE_RANK.get(b_gate, 0):
        reasons.append(f"gate改善：{b_gate} → {c_gate}")
    elif _GATE_RANK.get(c_gate, 0) < _GATE_RANK.get(b_gate, 0):
        reasons.append(f"gate悪化：{b_gate} → {c_gate}")
    else:
        reasons.append(f"gate同等：{b_gate}")

    if c_pnl > b_pnl:
        reasons.append(f"損益改善：{b_pnl}円 → {c_pnl}円")
    elif c_pnl < b_pnl:
        reasons.append(f"損益悪化：{b_pnl}円 → {c_pnl}円")
    else:
        reasons.append(f"損益同値：{b_pnl}円")

    if c_pf > b_pf:
        reasons.append(f"PF改善：{b_pf:.3f} → {c_pf:.3f}")
    elif c_pf < b_pf:
        reasons.append(f"PF悪化：{b_pf:.3f} → {c_pf:.3f}")
    else:
        reasons.append(f"PF同値：{b_pf:.3f}")

    if c_dd < b_dd:
        reasons.append(f"DD改善：{b_dd:.4f} → {c_dd:.4f}")
    elif c_dd > b_dd:
        reasons.append(f"DD悪化：{b_dd:.4f} → {c_dd:.4f}")
    else:
        reasons.append(f"DD同値：{b_dd:.4f}")

    if c_tr > b_tr:
        reasons.append(f"取引回数増：{b_tr} → {c_tr}")
    elif c_tr < b_tr:
        reasons.append(f"取引回数減：{b_tr} → {c_tr}")
    else:
        reasons.append(f"取引回数同値：{b_tr}")

    return {
        "evaluated_at": timezone.localtime(timezone.now()).isoformat(),
        "base": base,
        "cand": cand,
        "delta": {
            "pnl_sum_yen": int(c_pnl - b_pnl),
            "max_dd_pct": float(c_dd - b_dd),
            "pf_avg": float(c_pf - b_pf),
            "trades_sum": int(c_tr - b_tr),
        },
        "diagnosis": {
            "regime_warning": diagnosis.get("regime_warning"),
            "diagnosis_tags": list(diagnosis.get("diagnosis_tags") or []),
            "preferred_knobs": list(diagnosis.get("preferred_knobs") or []),
            "preferred_moves": list(diagnosis.get("preferred_moves") or [])[:8],
        },
        "reasons": reasons,
    }


def _extract_breakout_params(snapshot: AutoTradeSettingSnapshot) -> Dict[str, Any]:
    snap = snapshot.snapshot if isinstance(snapshot.snapshot, dict) else {}
    lab = snap.get("lab") if isinstance(snap.get("lab"), dict) else {}
    br = lab.get("BREAKOUT") if isinstance(lab.get("BREAKOUT"), dict) else {}

    rr_breakout = _get_rr_from_snapshot(snapshot)

    stop_pct = _safe_float(snap.get("breakout_stop_pct"), 0.0)
    if stop_pct <= 0:
        stop_pct_ui = _safe_float(br.get("stop_pct_ui"), 0.0)
        if stop_pct_ui > 0:
            stop_pct = stop_pct_ui / 100.0
    if stop_pct <= 0:
        stop_pct = 0.005

    lookback_bars = _safe_int(snap.get("breakout_lookback_bars"), 0)
    if lookback_bars <= 0:
        lookback_bars = _safe_int(br.get("lookback_bars"), 0)
    if lookback_bars <= 0:
        lookback_bars = 16

    max_hold_bars = _safe_int(snap.get("breakout_max_hold_bars"), 0)
    if max_hold_bars <= 0:
        max_hold_bars = _safe_int(snap.get("max_hold_bars"), 0)
    if max_hold_bars <= 0:
        max_hold_min = _safe_int(br.get("max_hold_min"), 0)
        if max_hold_min > 0:
            max_hold_bars = max(1, int(round(max_hold_min / 5)))
    if max_hold_bars <= 0:
        max_hold_bars = 6

    daily_filter = str(snap.get("breakout_daily_filter") or br.get("daily_filter") or "SMA").upper().strip()
    if daily_filter not in ["OFF", "SMA"]:
        daily_filter = "SMA"

    sma_days = _safe_int(snap.get("breakout_sma_days"), 0)
    if sma_days <= 0:
        sma_days = _safe_int(br.get("sma_days"), 20)
    if sma_days <= 0:
        sma_days = 20

    direction = str(snap.get("breakout_direction") or br.get("direction") or "TREND_ONLY").upper().strip()
    if direction not in ["TREND_ONLY", "BOTH"]:
        direction = "TREND_ONLY"

    base_equity_yen = _safe_int(
        snap.get("base_equity_yen"),
        _safe_int(getattr(settings, "AUTOTRADE_BASE_EQUITY_YEN", 1_000_000), 1_000_000),
    )

    return {
        "rr_breakout": float(rr_breakout),
        "stop_pct": float(stop_pct),
        "lookback_bars": int(lookback_bars),
        "max_hold_bars": int(max_hold_bars),
        "daily_filter": daily_filter,
        "sma_days": int(sma_days),
        "direction": direction,
        "base_equity_yen": int(base_equity_yen),
    }


def _apply_breakout_params(snapshot_dict: Dict[str, Any], params: Dict[str, Any]) -> Dict[str, Any]:
    out = deepcopy(snapshot_dict if isinstance(snapshot_dict, dict) else {})

    out["base_equity_yen"] = int(params["base_equity_yen"])
    out["rr_breakout"] = float(params["rr_breakout"])
    out["breakout_stop_pct"] = float(params["stop_pct"])
    out["breakout_lookback_bars"] = int(params["lookback_bars"])
    out["max_hold_bars"] = int(params["max_hold_bars"])
    out["breakout_max_hold_bars"] = int(params["max_hold_bars"])
    out["breakout_daily_filter"] = str(params["daily_filter"])
    out["breakout_sma_days"] = int(params["sma_days"])
    out["breakout_direction"] = str(params["direction"])

    lab = out.get("lab") if isinstance(out.get("lab"), dict) else {}
    br = lab.get("BREAKOUT") if isinstance(lab.get("BREAKOUT"), dict) else {}

    br["rr"] = float(params["rr_breakout"])
    br["stop_pct_ui"] = round(float(params["stop_pct"]) * 100.0, 3)
    br["lookback_bars"] = int(params["lookback_bars"])
    br["max_hold_min"] = int(params["max_hold_bars"]) * 5
    br["daily_filter"] = str(params["daily_filter"])
    br["sma_days"] = int(params["sma_days"])
    br["direction"] = str(params["direction"])

    lab["BREAKOUT"] = br
    out["lab"] = lab
    return out


def _params_key(params: Dict[str, Any]) -> Tuple[Any, ...]:
    return (
        round(_safe_float(params.get("rr_breakout"), 0.0), 6),
        round(_safe_float(params.get("stop_pct"), 0.0), 6),
        _safe_int(params.get("lookback_bars"), 0),
        _safe_int(params.get("max_hold_bars"), 0),
        str(params.get("daily_filter") or ""),
        _safe_int(params.get("sma_days"), 0),
        str(params.get("direction") or ""),
    )


def _build_candidate_specs(
    *,
    base_params: Dict[str, Any],
    gate_level: str,
    phase: str,
) -> List[Dict[str, Any]]:
    phase = str(phase or "MORNING").upper().strip()
    gate_level = str(gate_level or "STOP").upper().strip()

    if phase == "EOD" and gate_level == "STOP":
        rr_step = 0.25
        stop_step = 0.0010
        lookback_step = 4
        hold_step = 2
        sma_step = 10
    elif phase == "EOD":
        rr_step = 0.20
        stop_step = 0.0010
        lookback_step = 4
        hold_step = 2
        sma_step = 10
    elif gate_level == "FULL":
        rr_step = 0.10
        stop_step = 0.0005
        lookback_step = 2
        hold_step = 1
        sma_step = 5
    elif gate_level == "LIGHT":
        rr_step = 0.15
        stop_step = 0.0005
        lookback_step = 2
        hold_step = 1
        sma_step = 5
    else:
        rr_step = 0.20
        stop_step = 0.0010
        lookback_step = 4
        hold_step = 2
        sma_step = 10

    rr_min = _safe_float(getattr(settings, "AUTOTRADE_TUNE_RR_MIN", 1.0), 1.0)
    rr_max = _safe_float(getattr(settings, "AUTOTRADE_TUNE_RR_MAX", 3.5), 3.5)

    stop_min = _safe_float(getattr(settings, "AUTOTRADE_TUNE_STOP_PCT_MIN", 0.0025), 0.0025)
    stop_max = _safe_float(getattr(settings, "AUTOTRADE_TUNE_STOP_PCT_MAX", 0.0120), 0.0120)

    lookback_min = _safe_int(getattr(settings, "AUTOTRADE_TUNE_LOOKBACK_MIN", 6), 6)
    lookback_max = _safe_int(getattr(settings, "AUTOTRADE_TUNE_LOOKBACK_MAX", 30), 30)

    hold_min = _safe_int(getattr(settings, "AUTOTRADE_TUNE_MAX_HOLD_BARS_MIN", 3), 3)
    hold_max = _safe_int(getattr(settings, "AUTOTRADE_TUNE_MAX_HOLD_BARS_MAX", 18), 18)

    sma_min = _safe_int(getattr(settings, "AUTOTRADE_TUNE_SMA_MIN", 10), 10)
    sma_max = _safe_int(getattr(settings, "AUTOTRADE_TUNE_SMA_MAX", 75), 75)

    def clamp_rr(x: float) -> float:
        return max(rr_min, min(rr_max, float(x)))

    def clamp_stop(x: float) -> float:
        return max(stop_min, min(stop_max, float(x)))

    def clamp_lookback(x: int) -> int:
        return max(lookback_min, min(lookback_max, int(x)))

    def clamp_hold(x: int) -> int:
        return max(hold_min, min(hold_max, int(x)))

    def clamp_sma(x: int) -> int:
        return max(sma_min, min(sma_max, int(x)))

    specs: List[Dict[str, Any]] = []

    def add_spec(knob: str, delta_label: str, params_patch: Dict[str, Any]) -> None:
        params = deepcopy(base_params)
        params.update(params_patch)
        params["rr_breakout"] = clamp_rr(_safe_float(params["rr_breakout"], base_params["rr_breakout"]))
        params["stop_pct"] = clamp_stop(_safe_float(params["stop_pct"], base_params["stop_pct"]))
        params["lookback_bars"] = clamp_lookback(_safe_int(params["lookback_bars"], base_params["lookback_bars"]))
        params["max_hold_bars"] = clamp_hold(_safe_int(params["max_hold_bars"], base_params["max_hold_bars"]))
        params["sma_days"] = clamp_sma(_safe_int(params["sma_days"], base_params["sma_days"]))

        specs.append(
            {
                "knob": knob,
                "delta_label": delta_label,
                "params": params,
            }
        )

    add_spec("rr_breakout", f"+{rr_step:.3f}", {"rr_breakout": base_params["rr_breakout"] + rr_step})
    add_spec("rr_breakout", f"-{rr_step:.3f}", {"rr_breakout": base_params["rr_breakout"] - rr_step})

    add_spec("stop_pct", f"+{stop_step:.4f}", {"stop_pct": base_params["stop_pct"] + stop_step})
    add_spec("stop_pct", f"-{stop_step:.4f}", {"stop_pct": base_params["stop_pct"] - stop_step})

    add_spec("lookback_bars", f"+{lookback_step}", {"lookback_bars": base_params["lookback_bars"] + lookback_step})
    add_spec("lookback_bars", f"-{lookback_step}", {"lookback_bars": base_params["lookback_bars"] - lookback_step})

    add_spec("max_hold_bars", f"+{hold_step}", {"max_hold_bars": base_params["max_hold_bars"] + hold_step})
    add_spec("max_hold_bars", f"-{hold_step}", {"max_hold_bars": base_params["max_hold_bars"] - hold_step})

    add_spec("sma_days", f"+{sma_step}", {"sma_days": base_params["sma_days"] + sma_step})
    add_spec("sma_days", f"-{sma_step}", {"sma_days": base_params["sma_days"] - sma_step})

    toggled_filter = "OFF" if str(base_params["daily_filter"]).upper() == "SMA" else "SMA"
    add_spec("daily_filter", f"{base_params['daily_filter']}→{toggled_filter}", {"daily_filter": toggled_filter})

    toggled_direction = "BOTH" if str(base_params["direction"]).upper() == "TREND_ONLY" else "TREND_ONLY"
    add_spec("direction", f"{base_params['direction']}→{toggled_direction}", {"direction": toggled_direction})

    seen = set()
    uniq: List[Dict[str, Any]] = []
    for spec in specs:
        key = _params_key(spec["params"])
        if key in seen:
            continue
        seen.add(key)
        uniq.append(spec)

    return uniq


def _rank_tuple(pack: Dict[str, Any]) -> Tuple[int, int, int, int, int]:
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


def _is_better(base: Dict[str, Any], cand: Dict[str, Any]) -> bool:
    b = base.get("score") or {}
    c = cand.get("score") or {}

    b_tr = _safe_int(b.get("trades_sum"), 0)
    c_tr = _safe_int(c.get("trades_sum"), 0)

    b_dd = _safe_float(b.get("max_dd_pct"), 0.0)
    c_dd = _safe_float(c.get("max_dd_pct"), 0.0)

    if b_tr > 0 and c_tr < int(max(5, b_tr * 0.60)):
        return False

    if (c_dd - b_dd) > 0.006:
        return False

    return _rank_tuple(cand) > _rank_tuple(base)


def _is_reasonable_candidate(base: Dict[str, Any], cand: Dict[str, Any]) -> bool:
    b_gate = str(base.get("gate_level") or "STOP").upper().strip()
    c_gate = str(cand.get("gate_level") or "STOP").upper().strip()

    b = base.get("score") or {}
    c = cand.get("score") or {}

    b_tr = _safe_int(b.get("trades_sum"), 0)
    c_tr = _safe_int(c.get("trades_sum"), 0)

    b_dd = _safe_float(b.get("max_dd_pct"), 0.0)
    c_dd = _safe_float(c.get("max_dd_pct"), 0.0)

    if _GATE_RANK.get(c_gate, 0) < max(0, _GATE_RANK.get(b_gate, 0) - 1):
        return False

    if b_tr > 0 and c_tr < int(max(5, b_tr * 0.60)):
        return False

    if (c_dd - b_dd) > 0.008:
        return False

    return True


def _normalize_metrics_dict(raw_metrics: Dict[Any, Any], *, windows: List[int]) -> Dict[int, Dict[str, Dict[str, Any]]]:
    norm: Dict[int, Dict[str, Dict[str, Any]]] = {}
    for w in windows:
        w_int = int(w)
        d = raw_metrics.get(w_int) if isinstance(raw_metrics.get(w_int), dict) else raw_metrics.get(str(w_int))
        d = d if isinstance(d, dict) else {}
        norm[w_int] = {
            "BREAKOUT": d.get("BREAKOUT") if isinstance(d.get("BREAKOUT"), dict) else {},
        }
    return norm


def _pack_candidate_result(
    *,
    spec: Dict[str, Any],
    res: Dict[str, Any],
    windows: List[int],
    source_snapshot_id: int,
) -> Dict[str, Any]:
    metrics = (res.get("metrics") or {}) if isinstance(res, dict) else {}
    norm = _normalize_metrics_dict(metrics, windows=list(windows))
    score = _aggregate_score(norm, windows=list(windows))

    gate_obj = (res.get("gate") or {}) if isinstance(res, dict) else {}
    final_obj = gate_obj.get("final")
    if isinstance(final_obj, dict):
        final_level = str(final_obj.get("gate_level") or "STOP").upper().strip()
    else:
        final_level = str(final_obj or "STOP").upper().strip()

    return {
        "gate_level": final_level,
        "score": score,
        "params": deepcopy(spec.get("params") or {}),
        "knob": str(spec.get("knob") or ""),
        "delta_label": str(spec.get("delta_label") or ""),
        "source_snapshot_id": int(source_snapshot_id),
    }


def _snapshot_state_for_restore(state: AutoTradeDailyState) -> Dict[str, Any]:
    return {
        "backtest": deepcopy(state.backtest if isinstance(state.backtest, dict) else {}),
        "gate_level": str(state.gate_level or ""),
        "gate_reason": str(state.gate_reason or ""),
        "strategy": str(state.strategy or ""),
        "strategy_decision": deepcopy(state.strategy_decision if isinstance(state.strategy_decision, dict) else {}),
        "updated_at": state.updated_at,
    }


def _restore_state(state: AutoTradeDailyState, orig: Dict[str, Any]) -> None:
    state.backtest = deepcopy(orig.get("backtest") or {})
    state.gate_level = str(orig.get("gate_level") or "")
    state.gate_reason = str(orig.get("gate_reason") or "")
    state.strategy = str(orig.get("strategy") or "")
    state.strategy_decision = deepcopy(orig.get("strategy_decision") or {})
    state.updated_at = timezone.now()
    state.save(update_fields=["backtest", "gate_level", "gate_reason", "strategy", "strategy_decision", "updated_at"])


def _spec_matches_prefer(delta_label: str, prefer: str) -> bool:
    dl = str(delta_label or "")
    pf = str(prefer or "").upper().strip()

    if pf == "UP":
        return dl.startswith("+")
    if pf == "DOWN":
        return dl.startswith("-")
    if pf == "TO_SMA":
        return ("→SMA" in dl) or ("->SMA" in dl)
    if pf == "TO_OFF":
        return ("→OFF" in dl) or ("->OFF" in dl)
    if pf == "TO_TREND_ONLY":
        return ("→TREND_ONLY" in dl) or ("->TREND_ONLY" in dl)
    if pf == "TO_BOTH":
        return ("→BOTH" in dl) or ("->BOTH" in dl)
    return False


def _spec_preference_bonus(spec: Dict[str, Any], diagnosis: Dict[str, Any]) -> int:
    knob = str(spec.get("knob") or "")
    delta_label = str(spec.get("delta_label") or "")
    bonus = 0

    for row in list(diagnosis.get("preferred_moves") or []):
        if str(row.get("knob") or "") != knob:
            continue
        if _spec_matches_prefer(delta_label, str(row.get("prefer") or "")):
            bonus += max(1, _safe_int(row.get("weight"), 0))

    return int(bonus)


def _row_rank_tuple(row: Dict[str, Any]) -> Tuple[int, int, int, int, int, int]:
    pack = row.get("pack") if isinstance(row.get("pack"), dict) else {}
    core = _rank_tuple(pack)
    diagnosis_bonus = _safe_int(row.get("diagnosis_bonus"), 0)

    return (
        core[0],
        diagnosis_bonus,
        core[1],
        core[2],
        core[3],
        core[4],
    )


@transaction.atomic
def auto_tune_generate_candidate(
    *,
    target_date: Optional[dt_date] = None,
    windows: Optional[List[int]] = None,
    phase: str = "MORNING",
) -> AutoTuneResult:
    if target_date is None:
        target_date = timezone.localdate()

    phase = str(phase or "MORNING").upper().strip()
    if phase not in ["MORNING", "EOD"]:
        phase = "MORNING"

    if windows is None:
        windows = list(getattr(settings, "AUTOTRADE_BT_WINDOWS", [20, 40, 60]))
    windows = [int(x) for x in (windows or [])]

    state, _ = AutoTradeDailyState.objects.get_or_create(date=target_date)
    if is_emergency_stopped(state):
        return AutoTuneResult(
            ok=True,
            skipped=True,
            reason="emergency_stop",
            phase=phase,
            best_candidate_id=None,
            improved=False,
        )

    active = _get_active_snapshot()
    if not active:
        return AutoTuneResult(
            ok=False,
            skipped=True,
            reason="no_active_snapshot",
            phase=phase,
            best_candidate_id=None,
            improved=False,
        )

    uni = state.universe if isinstance(state.universe, dict) else {}
    picks = [x.get("ticker") for x in (uni.get("picks") or []) if isinstance(x, dict) and x.get("ticker")]
    picks = [str(x).strip() for x in (picks or []) if str(x).strip()]
    if not picks:
        return AutoTuneResult(
            ok=True,
            skipped=True,
            reason="no_picks",
            phase=phase,
            best_candidate_id=None,
            improved=False,
        )

    _clear_phase_generated_candidates(target_date=target_date, phase=phase)

    gate_bundle = _get_gate_bundle_from_state(state)
    base_metrics = _get_baseline_metrics_from_state(state, windows=list(windows))
    base_score = _aggregate_score(base_metrics, windows=list(windows))
    base_params = _extract_breakout_params(active)

    diagnosis = build_recent_diagnosis(
        target_date=target_date,
        phase=phase,
        mode="PAPER",
        strategy="BREAKOUT",
        base_equity_yen=int(base_params.get("base_equity_yen") or getattr(settings, "AUTOTRADE_BASE_EQUITY_YEN", 1_000_000)),
    )

    base_pack = {
        "gate_level": str(gate_bundle.get("final_level") or "STOP").upper().strip(),
        "score": base_score,
        "params": base_params,
        "active_snapshot_id": int(active.id),
        "diagnosis_tags": list(diagnosis.get("diagnosis_tags") or []),
        "regime_warning": str(diagnosis.get("regime_warning") or "NORMAL"),
    }

    source_snapshots: List[AutoTradeSettingSnapshot] = [active]
    if phase == "EOD":
        for cand in _iter_candidate_snapshots_for_date(target_date=target_date):
            if cand.id == active.id:
                continue
            source_snapshots.append(cand)

    uniq_sources: List[AutoTradeSettingSnapshot] = []
    seen_source_ids = set()
    for s in source_snapshots:
        if s.id in seen_source_ids:
            continue
        seen_source_ids.add(s.id)
        uniq_sources.append(s)
    source_snapshots = uniq_sources

    created_ids: List[int] = []
    candidate_rows: List[Dict[str, Any]] = []

    orig_state = _snapshot_state_for_restore(state)
    seen_generated_param_keys = set()

    try:
        for source in source_snapshots:
            source_params = _extract_breakout_params(source)
            specs = _build_candidate_specs(
                base_params=source_params,
                gate_level=str(gate_bundle.get("final_level") or "STOP"),
                phase=phase,
            )

            for idx, spec in enumerate(specs, 1):
                pkey = _params_key(spec["params"])
                if pkey in seen_generated_param_keys:
                    continue
                seen_generated_param_keys.add(pkey)

                snap_dict = source.snapshot if isinstance(source.snapshot, dict) else {}
                new_snap = _apply_breakout_params(snap_dict, spec["params"])

                new_snap["tune"] = {
                    "target_date": str(target_date),
                    "phase": phase,
                    "knob": str(spec["knob"]),
                    "delta_label": str(spec["delta_label"]),
                    "rank_order": int(idx),
                    "based_on_active_id": int(active.id),
                    "based_on_source_id": int(source.id),
                    "note": "auto_tune_generate_candidate",
                }

                label = f"AUTO_TUNE {target_date} [{phase}] src{source.id} {spec['knob']} ({spec['delta_label']})"
                cand = AutoTradeSettingSnapshot.objects.create(
                    user=source.user,
                    source_profile=source.source_profile,
                    label=label,
                    status="CANDIDATE",
                    snapshot=new_snap,
                )
                created_ids.append(int(cand.id))

                res = run_detailed_backtests_for_universe(
                    snapshot=cand,
                    picks=picks,
                    target_date=target_date,
                    windows=tuple(int(x) for x in windows),
                    rr_breakout=None,
                    base_equity_yen=int(source_params.get("base_equity_yen") or getattr(settings, "AUTOTRADE_BASE_EQUITY_YEN", 1_000_000)),
                    force=True,
                )

                cand_pack = _pack_candidate_result(
                    spec=spec,
                    res=res if isinstance(res, dict) else {},
                    windows=list(windows),
                    source_snapshot_id=int(source.id),
                )

                better = _is_better(base_pack, cand_pack)
                reasonable = _is_reasonable_candidate(base_pack, cand_pack)
                diagnosis_bonus = _spec_preference_bonus(spec, diagnosis)

                snap_now = cand.snapshot if isinstance(cand.snapshot, dict) else {}
                tune_eval = _build_tune_eval(base_pack, cand_pack, diagnosis)
                tune_eval["improved_vs_active"] = bool(better)
                tune_eval["reasonable_candidate"] = bool(reasonable)
                tune_eval["diagnosis_bonus"] = int(diagnosis_bonus)
                snap_now["tune_eval"] = tune_eval
                cand.snapshot = snap_now
                cand.save(update_fields=["snapshot"])

                candidate_rows.append(
                    {
                        "cand": cand,
                        "pack": cand_pack,
                        "better": bool(better),
                        "reasonable": bool(reasonable),
                        "diagnosis_bonus": int(diagnosis_bonus),
                    }
                )

    finally:
        _restore_state(state, orig_state)

    if phase == "EOD":
        for src in source_snapshots:
            if src.id == active.id:
                continue
            if src.status == "CANDIDATE":
                _retire_snapshot(src, note="eod_source_consumed_by_retune")

    if not candidate_rows:
        return AutoTuneResult(
            ok=True,
            skipped=True,
            reason="no_candidate_rows",
            phase=phase,
            best_candidate_id=None,
            improved=False,
            created_candidate_ids=created_ids,
            base=base_pack,
            diagnosis=diagnosis,
        )

    improved_rows = [x for x in candidate_rows if x["better"]]
    sorted_rows = sorted(candidate_rows, key=_row_rank_tuple, reverse=True)

    best_improved_row = max(improved_rows, key=_row_rank_tuple) if improved_rows else None
    improved = bool(best_improved_row)

    retained_rows: List[Dict[str, Any]] = []

    if phase == "MORNING":
        keep_max = int(getattr(settings, "AUTOTRADE_MORNING_CANDIDATE_KEEP_MAX", 3))

        seen_ids = set()
        for row in sorted(improved_rows, key=_row_rank_tuple, reverse=True):
            cid = int(row["cand"].id)
            if cid in seen_ids:
                continue
            retained_rows.append(row)
            seen_ids.add(cid)
            if len(retained_rows) >= keep_max:
                break

        if len(retained_rows) < keep_max:
            for row in sorted_rows:
                cid = int(row["cand"].id)
                if cid in seen_ids:
                    continue
                if not row["reasonable"]:
                    continue
                retained_rows.append(row)
                seen_ids.add(cid)
                if len(retained_rows) >= keep_max:
                    break
    else:
        if best_improved_row is not None:
            retained_rows = [best_improved_row]
        else:
            retained_rows = []

    retained_ids = [int(x["cand"].id) for x in retained_rows]
    best_candidate_id = int(best_improved_row["cand"].id) if best_improved_row is not None else None

    for row in candidate_rows:
        cand = row["cand"]
        snap_now = cand.snapshot if isinstance(cand.snapshot, dict) else {}

        if int(cand.id) in retained_ids:
            cand.status = "CANDIDATE"
            snap_now["tune_selected"] = True
            snap_now["tune_improved"] = bool(row["better"])
            cand.snapshot = snap_now
            cand.save(update_fields=["status", "snapshot"])
        else:
            cand.status = "RETIRED"
            snap_now["tune_selected"] = False
            snap_now["tune_rejected"] = True
            cand.snapshot = snap_now
            cand.save(update_fields=["status", "snapshot"])

    best_pack = best_improved_row["pack"] if best_improved_row is not None else {}
    reason = "best_candidate_ready" if best_improved_row is not None else "no_better_candidate"

    return AutoTuneResult(
        ok=True,
        skipped=False,
        reason=reason,
        phase=phase,
        best_candidate_id=best_candidate_id,
        improved=improved,
        retained_candidate_ids=retained_ids,
        created_candidate_ids=created_ids,
        base=base_pack,
        best=best_pack,
        diagnosis=diagnosis,
    )