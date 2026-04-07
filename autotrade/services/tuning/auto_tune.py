"""
[FILE] autotrade/services/tuning/auto_tune.py
[PATH] <project_root>/autotrade/services/tuning/auto_tune.py

このファイルは何？
- 自動チューニングの本体です。
- ACTIVE を基準に複数ノブを一気に試し、その日いちばん良い候補を 1つ残します。

今回の変更：
- 1ノブ固定ではなく、複数ノブを全部試す
- 朝フェーズ（MORNING）と引け後救済フェーズ（EOD_STOP）を分離
- 候補評価中に DailyState を壊さないよう、state を退避→復元する
- 朝は候補作成のみ
- 引け後STOP救済は、この中で最良候補を1つ残せるようにする
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


_GATE_RANK = {"STOP": 0, "LIGHT": 1, "FULL": 2}


@dataclass(frozen=True)
class AutoTuneResult:
    ok: bool
    skipped: bool
    reason: str
    phase: str
    best_candidate_id: Optional[int]
    improved: bool
    created_candidate_ids: List[int] = field(default_factory=list)
    base: Dict[str, Any] = field(default_factory=dict)
    best: Dict[str, Any] = field(default_factory=dict)


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


def _get_gate_bundle_from_state(state: AutoTradeDailyState) -> Dict[str, Any]:
    """
    runner.py が作った gate を読む。
    """
    bt = state.backtest if isinstance(state.backtest, dict) else {}
    gate = bt.get("gate") if isinstance(bt.get("gate"), dict) else {}
    final = gate.get("final") if isinstance(gate.get("final"), dict) else {}

    final_level = str(final.get("gate_level") or state.gate_level or "STOP").upper().strip()

    breakout = gate.get("BREAKOUT") if isinstance(gate.get("BREAKOUT"), dict) else {}
    lv_b = str(breakout.get("gate_level") or final_level or "STOP").upper().strip()

    active: List[str]
    disabled: List[str]

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


def _get_rr_from_state_or_snapshot(state: AutoTradeDailyState, active: AutoTradeSettingSnapshot) -> float:
    """
    rr_breakout は state.backtest['meta'] → snapshot → settings の順で読む。
    """
    bt = state.backtest if isinstance(state.backtest, dict) else {}
    meta = bt.get("meta") if isinstance(bt.get("meta"), dict) else {}

    rr_b = _safe_float(meta.get("rr_breakout"), 0.0)
    if rr_b > 0:
        return rr_b

    snap = active.snapshot if isinstance(active.snapshot, dict) else {}
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
    """
    baseline は朝の runner が作った state.backtest['by_window'] を読む。
    """
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
    """
    BREAKOUT 一本運用の比較用スコア。
    """
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


def _build_tune_eval(base: Dict[str, Any], cand: Dict[str, Any]) -> Dict[str, Any]:
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
        "reasons": reasons,
    }


def _extract_breakout_params(active: AutoTradeSettingSnapshot, state: AutoTradeDailyState) -> Dict[str, Any]:
    snap = active.snapshot if isinstance(active.snapshot, dict) else {}
    lab = snap.get("lab") if isinstance(snap.get("lab"), dict) else {}
    br = lab.get("BREAKOUT") if isinstance(lab.get("BREAKOUT"), dict) else {}

    rr_breakout = _get_rr_from_state_or_snapshot(state, active)

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

    base_equity_yen = _safe_int(snap.get("base_equity_yen"), _safe_int(getattr(settings, "AUTOTRADE_BASE_EQUITY_YEN", 1_000_000), 1_000_000))

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
    state: AutoTradeDailyState,
    active: AutoTradeSettingSnapshot,
    phase: str,
) -> List[Dict[str, Any]]:
    """
    全ノブを試す。
    """
    phase = str(phase or "MORNING").upper().strip()
    gate_bundle = _get_gate_bundle_from_state(state)
    gate_level = str(gate_bundle.get("final_level") or "STOP").upper().strip()

    base = _extract_breakout_params(active, state)

    if phase == "EOD_STOP":
        rr_step = 0.25
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
        params = deepcopy(base)
        params.update(params_patch)
        params["rr_breakout"] = clamp_rr(_safe_float(params["rr_breakout"], base["rr_breakout"]))
        params["stop_pct"] = clamp_stop(_safe_float(params["stop_pct"], base["stop_pct"]))
        params["lookback_bars"] = clamp_lookback(_safe_int(params["lookback_bars"], base["lookback_bars"]))
        params["max_hold_bars"] = clamp_hold(_safe_int(params["max_hold_bars"], base["max_hold_bars"]))
        params["sma_days"] = clamp_sma(_safe_int(params["sma_days"], base["sma_days"]))

        specs.append({
            "knob": knob,
            "delta_label": delta_label,
            "params": params,
        })

    # 数値ノブ
    add_spec("rr_breakout", f"+{rr_step:.3f}", {"rr_breakout": base["rr_breakout"] + rr_step})
    add_spec("rr_breakout", f"-{rr_step:.3f}", {"rr_breakout": base["rr_breakout"] - rr_step})

    add_spec("stop_pct", f"+{stop_step:.4f}", {"stop_pct": base["stop_pct"] + stop_step})
    add_spec("stop_pct", f"-{stop_step:.4f}", {"stop_pct": base["stop_pct"] - stop_step})

    add_spec("lookback_bars", f"+{lookback_step}", {"lookback_bars": base["lookback_bars"] + lookback_step})
    add_spec("lookback_bars", f"-{lookback_step}", {"lookback_bars": base["lookback_bars"] - lookback_step})

    add_spec("max_hold_bars", f"+{hold_step}", {"max_hold_bars": base["max_hold_bars"] + hold_step})
    add_spec("max_hold_bars", f"-{hold_step}", {"max_hold_bars": base["max_hold_bars"] - hold_step})

    add_spec("sma_days", f"+{sma_step}", {"sma_days": base["sma_days"] + sma_step})
    add_spec("sma_days", f"-{sma_step}", {"sma_days": base["sma_days"] - sma_step})

    # カテゴリノブ
    toggled_filter = "OFF" if str(base["daily_filter"]).upper() == "SMA" else "SMA"
    add_spec("daily_filter", f"{base['daily_filter']}→{toggled_filter}", {"daily_filter": toggled_filter})

    toggled_direction = "BOTH" if str(base["direction"]).upper() == "TREND_ONLY" else "TREND_ONLY"
    add_spec("direction", f"{base['direction']}→{toggled_direction}", {"direction": toggled_direction})

    # 重複除去
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
    """
    比較優先度
    1) gate
    2) PF
    3) 損益
    4) DD（小さい方がよい）
    5) 取引回数
    """
    score = pack.get("score") or {}
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
    """
    旧ACTIVEより良いかを判定する。
    - gate優先
    - 同gateなら PF / 損益 / DD / 回数で比較
    - trades激減 / DD急悪化 は弾く
    """
    b = base.get("score") or {}
    c = cand.get("score") or {}

    b_tr = _safe_int(b.get("trades_sum"), 0)
    c_tr = _safe_int(c.get("trades_sum"), 0)

    b_dd = _safe_float(b.get("max_dd_pct"), 0.0)
    c_dd = _safe_float(c.get("max_dd_pct"), 0.0)

    # 取引回数が極端に減る候補は落とす
    if b_tr > 0 and c_tr < int(max(5, b_tr * 0.60)):
        return False

    # DDが急激に悪化する候補は落とす
    if (c_dd - b_dd) > 0.006:
        return False

    return _rank_tuple(cand) > _rank_tuple(base)


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
    if phase not in ["MORNING", "EOD_STOP"]:
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

    active = (
        AutoTradeSettingSnapshot.objects
        .filter(status="ACTIVE")
        .order_by("-created_at")
        .first()
    )
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

    gate_bundle = _get_gate_bundle_from_state(state)
    base_metrics = _get_baseline_metrics_from_state(state, windows=list(windows))
    base_score = _aggregate_score(base_metrics, windows=list(windows))
    base_params = _extract_breakout_params(active, state)

    base_pack = {
        "gate_level": str(gate_bundle.get("final_level") or "STOP").upper().strip(),
        "score": base_score,
        "params": base_params,
    }

    specs = _build_candidate_specs(state=state, active=active, phase=phase)
    if not specs:
        return AutoTuneResult(
            ok=True,
            skipped=True,
            reason="no_tunable_spec",
            phase=phase,
            best_candidate_id=None,
            improved=False,
            base=base_pack,
        )

    created_ids: List[int] = []
    candidate_rows: List[Dict[str, Any]] = []

    orig_state = _snapshot_state_for_restore(state)

    try:
        for idx, spec in enumerate(specs, 1):
            snap_dict = active.snapshot if isinstance(active.snapshot, dict) else {}
            new_snap = _apply_breakout_params(snap_dict, spec["params"])

            new_snap["tune"] = {
                "target_date": str(target_date),
                "phase": phase,
                "knob": str(spec["knob"]),
                "delta_label": str(spec["delta_label"]),
                "rank_order": int(idx),
                "based_on_active_id": int(active.id),
                "note": "auto_tune_generate_candidate",
            }

            label = f"AUTO_TUNE {target_date} [{phase}] {spec['knob']} ({spec['delta_label']})"
            cand = AutoTradeSettingSnapshot.objects.create(
                user=active.user,
                source_profile=active.source_profile,
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
                base_equity_yen=int(base_params.get("base_equity_yen") or getattr(settings, "AUTOTRADE_BASE_EQUITY_YEN", 1_000_000)),
                force=True,
            )

            cand_pack = _pack_candidate_result(spec=spec, res=res if isinstance(res, dict) else {}, windows=list(windows))
            better = _is_better(base_pack, cand_pack)

            snap_now = cand.snapshot if isinstance(cand.snapshot, dict) else {}
            snap_now["tune_eval"] = _build_tune_eval(base_pack, cand_pack)
            snap_now["tune_eval"]["improved_vs_active"] = bool(better)
            cand.snapshot = snap_now
            cand.save(update_fields=["snapshot"])

            candidate_rows.append({
                "cand": cand,
                "pack": cand_pack,
                "better": bool(better),
            })

    finally:
        # 候補評価で DailyState を上書きしない
        _restore_state(state, orig_state)

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
        )

    improved_rows = [x for x in candidate_rows if x["better"]]
    best_row_all = max(candidate_rows, key=lambda x: _rank_tuple(x["pack"]))
    best_row = max(improved_rows, key=lambda x: _rank_tuple(x["pack"])) if improved_rows else best_row_all
    improved = bool(improved_rows)

    # 1つだけ残し、それ以外は RETIRED にする
    best_candidate_id = int(best_row["cand"].id)

    for row in candidate_rows:
        cand = row["cand"]
        snap_now = cand.snapshot if isinstance(cand.snapshot, dict) else {}

        if int(cand.id) == best_candidate_id:
            cand.status = "CANDIDATE"
            snap_now["tune_selected"] = True
            snap_now["tune_improved"] = bool(improved)
            cand.snapshot = snap_now
            cand.save(update_fields=["status", "snapshot"])
        else:
            cand.status = "RETIRED"
            snap_now["tune_selected"] = False
            snap_now["tune_rejected"] = True
            cand.snapshot = snap_now
            cand.save(update_fields=["status", "snapshot"])

    return AutoTuneResult(
        ok=True,
        skipped=False,
        reason="best_candidate_ready" if improved else "best_candidate_not_better",
        phase=phase,
        best_candidate_id=best_candidate_id,
        improved=improved,
        created_candidate_ids=created_ids,
        base=base_pack,
        best=best_row["pack"],
    )