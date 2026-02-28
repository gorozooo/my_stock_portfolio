# =========================================================
# [FILE] autotrade/services/tuning/auto_tune.py
# [PATH] <project_root>/autotrade/services/tuning/auto_tune.py
#
# このファイルは何？
# - 自動チューニング（最小構成）の本体です。
# - 毎朝1ノブだけ動かし、候補Snapshot（CANDIDATE）を作って同日Executionで検証します。
#
# 今回の変更（攻め型 / RR探索幅の再設計）：
# - gateがSTOPほど探索幅を広げる（山を探す）
# - LIGHTは中くらい
# - FULLは触らない（勝ってる日は触るな）
# - 候補の試行順は「攻め方向（RR↑）→控えめ（RR↓）」にする
# =========================================================

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
from datetime import date as dt_date

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
    created_candidate_id: Optional[int]
    knob: Optional[str]
    base: Dict[str, Any]
    cand: Dict[str, Any]


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
    runner.py が作った新フォーマットの gate を読む（BREAKOUTのみ評価）
    """
    bt = state.backtest if isinstance(state.backtest, dict) else {}
    gate = bt.get("gate") if isinstance(bt.get("gate"), dict) else {}
    final = gate.get("final") if isinstance(gate.get("final"), dict) else {}

    # runnerが "final" を作っていない互換ケースでも落ちないように
    final_level = str(final.get("gate_level") or state.gate_level or "STOP")

    breakout = gate.get("BREAKOUT") if isinstance(gate.get("BREAKOUT"), dict) else {}
    lv_b = str(breakout.get("gate_level") or final_level or "STOP")

    active: List[str] = []
    disabled: List[str] = []

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


def _get_rr_from_state(state: AutoTradeDailyState) -> float:
    """
    rr_breakout は state.backtest['meta'] を優先。無ければsettings。
    """
    bt = state.backtest if isinstance(state.backtest, dict) else {}
    meta = bt.get("meta") if isinstance(bt.get("meta"), dict) else {}
    rr_b = _safe_float(meta.get("rr_breakout"), _safe_float(getattr(settings, "AUTOTRADE_RR_BREAKOUT", 2.0), 2.0))
    return rr_b


def _get_baseline_metrics_from_state(state: AutoTradeDailyState, *, windows: List[int]) -> Dict[str, Any]:
    """
    baseline は、朝の runner が既に作った state.backtest['by_window'] を読む（再計算しない）。
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
    BREAKOUT一本運用：PF/DD/損益/回数の“4本柱”を BREAKOUT で集計して比較しやすい形にする。
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
    """
    UIに出すための「落第理由」を文字列でまとめる（BREAKOUT基準）
    """
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

    if b_tr > 0 and c_tr < int(b_tr * 0.70):
        reasons.append(f"取引回数が減りすぎ：{b_tr} → {c_tr}（70%未満）")

    if (c_dd - b_dd) > 0.004:
        reasons.append(f"最大DDが悪化：{b_dd:.4f} → {c_dd:.4f}（+0.004超）")

    if c_pf + 1e-9 < b_pf:
        reasons.append(f"PFが悪化：{b_pf:.3f} → {c_pf:.3f}")

    if c_pnl > b_pnl:
        reasons.append(f"損益が改善：{b_pnl}円 → {c_pnl}円")
    elif c_pnl < b_pnl:
        reasons.append(f"損益が悪化：{b_pnl}円 → {c_pnl}円")
    else:
        reasons.append(f"損益が同じ：{b_pnl}円 → {c_pnl}円")

    delta = {
        "pnl_sum_yen": int(c_pnl - b_pnl),
        "max_dd_pct": float(c_dd - b_dd),
        "pf_avg": float(c_pf - b_pf),
        "trades_sum": int(c_tr - b_tr),
    }

    return {
        "evaluated_at": timezone.localtime(timezone.now()).isoformat(),
        "base": {"gate_level": b_gate, "score": {"pnl_sum_yen": b_pnl, "max_dd_pct": b_dd, "pf_avg": b_pf, "trades_sum": b_tr}, "rr": base.get("rr") or {}},
        "cand": {"gate_level": c_gate, "score": {"pnl_sum_yen": c_pnl, "max_dd_pct": c_dd, "pf_avg": c_pf, "trades_sum": c_tr}, "rr": cand.get("rr") or {}},
        "delta": delta,
        "reasons": reasons,
    }


def _is_improvement(base: Dict[str, Any], cand: Dict[str, Any]) -> bool:
    """
    改善条件（攻め型 / BREAKOUT基準）
    - gateが上がれば即OK
    - gate同等なら「PF or PnLが改善」優先
    - ただし DDが極端悪化 / trades激減 はNG
    """
    b_gate = str(base.get("gate_level") or "STOP")
    c_gate = str(cand.get("gate_level") or "STOP")

    if _GATE_RANK.get(c_gate, 0) > _GATE_RANK.get(b_gate, 0):
        return True
    if _GATE_RANK.get(c_gate, 0) < _GATE_RANK.get(b_gate, 0):
        return False

    b = base.get("score") or {}
    c = cand.get("score") or {}

    b_pnl = _safe_int(b.get("pnl_sum_yen"), 0)
    c_pnl = _safe_int(c.get("pnl_sum_yen"), 0)

    b_dd = _safe_float(b.get("max_dd_pct"), 1.0)
    c_dd = _safe_float(c.get("max_dd_pct"), 1.0)

    b_pf = _safe_float(b.get("pf_avg"), 0.0)
    c_pf = _safe_float(c.get("pf_avg"), 0.0)

    b_tr = _safe_int(b.get("trades_sum"), 0)
    c_tr = _safe_int(c.get("trades_sum"), 0)

    if b_tr > 0 and c_tr < int(b_tr * 0.70):
        return False

    # 攻め型なのでDD許容は少し広げるが、悪化しすぎはNG
    if (c_dd - b_dd) > 0.004:
        return False

    # PFが落ちるのは基本NG（攻め型の芯）
    if c_pf + 1e-9 < b_pf:
        return False

    # 攻め型：PnL改善が最優先、次点PF改善
    if c_pnl > b_pnl:
        return True

    if c_pnl == b_pnl and (c_pf > b_pf) and (c_dd <= b_dd + 1e-9):
        return True

    return False


def _pick_knob_and_candidates(state: AutoTradeDailyState) -> List[Dict[str, Any]]:
    """
    攻め型：1日1ノブ（rr_breakout）だけ動かすが、探索幅を gate で変える。
    - STOP：広め（山を探す）
    - LIGHT：中くらい
    - FULL：触らない
    """
    gate = _get_gate_bundle_from_state(state)
    lv_b = str((gate.get("breakout") or {}).get("gate_level") or gate.get("final_level") or "STOP")

    rr_b = _get_rr_from_state(state)

    rr_min = float(getattr(settings, "AUTOTRADE_TUNE_RR_MIN", 1.0))
    rr_max = float(getattr(settings, "AUTOTRADE_TUNE_RR_MAX", 3.2))  # ★攻め型で上限を少し上へ

    # gate別 step（デフォルト）
    step_stop = float(getattr(settings, "AUTOTRADE_TUNE_RR_STEP_STOP", 0.20))
    step_light = float(getattr(settings, "AUTOTRADE_TUNE_RR_STEP_LIGHT", 0.15))
    step_full = float(getattr(settings, "AUTOTRADE_TUNE_RR_STEP_FULL", 0.05))

    def clamp_rr(x: float) -> float:
        return max(rr_min, min(rr_max, float(x)))

    if lv_b == "FULL":
        return []

    if lv_b == "STOP":
        step = step_stop
    elif lv_b == "LIGHT":
        step = step_light
    else:
        step = step_full

    # 試行順：攻め（RR↑）→控えめ（RR↓）
    return [
        {"knob": "rr_breakout", "rr_breakout": clamp_rr(rr_b + step), "delta": +step},
        {"knob": "rr_breakout", "rr_breakout": clamp_rr(rr_b - step), "delta": -step},
    ]


@transaction.atomic
def auto_tune_generate_candidate(
    *,
    target_date: Optional[dt_date] = None,
    windows: Optional[List[int]] = None,
) -> AutoTuneResult:
    if target_date is None:
        target_date = timezone.localdate()

    if windows is None:
        windows = list(getattr(settings, "AUTOTRADE_BT_WINDOWS", [20, 60]))

    state, _ = AutoTradeDailyState.objects.get_or_create(date=target_date)
    if is_emergency_stopped(state):
        return AutoTuneResult(ok=True, skipped=True, reason="emergency_stop", created_candidate_id=None, knob=None, base={}, cand={})

    active = (
        AutoTradeSettingSnapshot.objects
        .filter(status="ACTIVE")
        .order_by("-created_at")
        .first()
    )
    if not active:
        return AutoTuneResult(ok=False, skipped=True, reason="no_active_snapshot", created_candidate_id=None, knob=None, base={}, cand={})

    exists_today_any = AutoTradeSettingSnapshot.objects.filter(snapshot__tune__target_date=str(target_date)).exists()
    if exists_today_any:
        return AutoTuneResult(ok=True, skipped=True, reason="tune_already_exists_today", created_candidate_id=None, knob=None, base={}, cand={})

    uni = state.universe if isinstance(state.universe, dict) else {}
    picks = [x.get("ticker") for x in (uni.get("picks") or []) if isinstance(x, dict) and x.get("ticker")]
    picks = [str(x).strip() for x in (picks or []) if str(x).strip()]
    if not picks:
        return AutoTuneResult(ok=True, skipped=True, reason="no_picks", created_candidate_id=None, knob=None, base={}, cand={})

    gate_bundle = _get_gate_bundle_from_state(state)
    base_metrics = _get_baseline_metrics_from_state(state, windows=list(windows))
    base_score = _aggregate_score(base_metrics, windows=list(windows))
    rr_b0 = _get_rr_from_state(state)

    base_pack = {
        "gate_level": str(gate_bundle.get("final_level") or "STOP"),
        "score": base_score,
        "rr": {"BREAKOUT": rr_b0},
    }

    knobs = _pick_knob_and_candidates(state)
    if not knobs:
        return AutoTuneResult(ok=True, skipped=True, reason="no_tunable_knob_today", created_candidate_id=None, knob=None, base=base_pack, cand={})

    for k in knobs:
        knob = str(k.get("knob") or "")
        rr_b = float(k.get("rr_breakout"))
        delta = float(k.get("delta") or 0.0)

        snap_dict = active.snapshot if isinstance(active.snapshot, dict) else {}
        new_snap = dict(snap_dict)

        new_snap["tune"] = {
            "target_date": str(target_date),
            "knob": knob,
            "delta": delta,
            "rr_breakout": float(rr_b),
            "based_on_active_id": int(active.id),
            "note": "auto_tune_generate_candidate",
        }

        label = f"AUTO_TUNE {target_date} {knob} ({delta:+.6f})"
        cand = AutoTradeSettingSnapshot.objects.create(
            user=active.user,
            source_profile=active.source_profile,
            label=label,
            status="CANDIDATE",
            snapshot=new_snap,
        )

        res = run_detailed_backtests_for_universe(
            snapshot=cand,
            picks=picks,
            target_date=target_date,
            windows=tuple(int(x) for x in windows),
            rr_breakout=float(rr_b),
            base_equity_yen=int(getattr(settings, "AUTOTRADE_BASE_EQUITY_YEN", 1_000_000)),
            force=True,
        )

        cand_metrics = (res.get("metrics") or {}) if isinstance(res, dict) else {}

        norm: Dict[int, Dict[str, Dict[str, Any]]] = {}
        for w in windows:
            w_int = int(w)
            d = cand_metrics.get(w_int) if isinstance(cand_metrics.get(w_int), dict) else cand_metrics.get(str(w_int))
            d = d if isinstance(d, dict) else {}
            norm[w_int] = {
                "BREAKOUT": d.get("BREAKOUT") if isinstance(d.get("BREAKOUT"), dict) else {},
            }

        cand_score = _aggregate_score(norm, windows=list(windows))

        gate = (res.get("gate") or {}) if isinstance(res, dict) else {}
        final_obj = gate.get("final")
        if isinstance(final_obj, dict):
            final_level = str(final_obj.get("gate_level") or "STOP")
        else:
            final_level = str(final_obj or "STOP")

        cand_pack = {
            "gate_level": final_level,
            "score": cand_score,
            "rr": {"BREAKOUT": rr_b},
        }

        snap_now = cand.snapshot if isinstance(cand.snapshot, dict) else {}
        snap_now["tune_eval"] = _build_tune_eval(base_pack, cand_pack)
        cand.snapshot = snap_now
        cand.save(update_fields=["snapshot"])

        if _is_improvement(base_pack, cand_pack):
            return AutoTuneResult(ok=True, skipped=False, reason="candidate_created", created_candidate_id=int(cand.id), knob=knob, base=base_pack, cand=cand_pack)

        cand.status = "RETIRED"
        snap_now = cand.snapshot if isinstance(cand.snapshot, dict) else {}
        snap_now["tune_rejected"] = True
        cand.snapshot = snap_now
        cand.save(update_fields=["status", "snapshot"])

    return AutoTuneResult(ok=True, skipped=True, reason="no_improving_candidate", created_candidate_id=None, knob=None, base=base_pack, cand={})