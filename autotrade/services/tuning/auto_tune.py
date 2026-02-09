"""
[FILE] autotrade/services/tuning/auto_tune.py
[PATH] <project_root>/autotrade/services/tuning/auto_tune.py

このファイルは何？
- 設計V/W：自動チューニング（最小構成）の本体です。
- “毎朝ちょっとだけ”候補設定（CANDIDATE Snapshot）を作り、同日バックテスト（Execution）で検証します。
- 採用（ACTIVE昇格）は別部品 auto_promote.py が担当（= 世代交代＆証拠焼き付け）。

今回の修正ポイント（重要）：
1) 同じ日に「候補を何度も作ってしまう」穴を塞ぐ
   - status=CANDIDATE のみ判定だと、RETIREDになった瞬間に“今日の候補なし扱い”になり再作成される。
   - → snapshot__tune__target_date で「status問わず」存在チェックし、同日は1回だけに固定。

2) 不合格（RETIRED）になった場合も、落第理由を追えるように
   - base_pack / cand_pack を snapshot["tune_eval"] に保存（再現性・説明責任）。
"""

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


# ---- gateレベルの序列（比較用）----
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
    runner.py が作った新フォーマットの gate を読む。
    """
    bt = state.backtest if isinstance(state.backtest, dict) else {}
    gate = bt.get("gate") if isinstance(bt.get("gate"), dict) else {}
    final = gate.get("final") if isinstance(gate.get("final"), dict) else {}

    return {
        "final_level": str(final.get("gate_level") or state.gate_level or "STOP"),
        "active": list(final.get("active") or []),
        "disabled": list(final.get("disabled") or []),
        "vwap": gate.get("VWAP") if isinstance(gate.get("VWAP"), dict) else {},
        "breakout": gate.get("BREAKOUT") if isinstance(gate.get("BREAKOUT"), dict) else {},
    }


def _get_rr_from_state(state: AutoTradeDailyState) -> Tuple[float, float]:
    """
    rrは state.backtest['meta'] を優先。無ければsettings。
    """
    bt = state.backtest if isinstance(state.backtest, dict) else {}
    meta = bt.get("meta") if isinstance(bt.get("meta"), dict) else {}

    rr_b = _safe_float(meta.get("rr_breakout"), _safe_float(getattr(settings, "AUTOTRADE_RR_BREAKOUT", 2.0), 2.0))
    rr_v = _safe_float(meta.get("rr_vwap"), _safe_float(getattr(settings, "AUTOTRADE_RR_VWAP", 1.5), 1.5))
    return rr_b, rr_v


def _get_stop_pct_vwap_from_snapshot(snapshot_dict: Dict[str, Any]) -> float:
    """
    VWAPの stop_pct を snapshot からなるべく拾う。
    どのキーで持っていても対応できるように“読めるだけ読む”。
    """
    candidates: List[Any] = []

    # 1) 直下
    candidates.append(snapshot_dict.get("stop_pct_vwap"))
    candidates.append(snapshot_dict.get("vwap_stop_pct"))
    candidates.append(snapshot_dict.get("stop_pct"))

    # 2) vwap dict
    vwap = snapshot_dict.get("vwap") if isinstance(snapshot_dict.get("vwap"), dict) else {}
    candidates.append(vwap.get("stop_pct"))
    candidates.append(vwap.get("stop_pct_vwap"))
    candidates.append(vwap.get("sl_pct"))

    # 3) params dict
    params = snapshot_dict.get("params") if isinstance(snapshot_dict.get("params"), dict) else {}
    pv = params.get("vwap") if isinstance(params.get("vwap"), dict) else {}
    candidates.append(pv.get("stop_pct"))
    candidates.append(pv.get("stop_pct_vwap"))
    candidates.append(pv.get("sl_pct"))

    fallback = _safe_float(getattr(settings, "AUTOTRADE_VWAP_STOP_PCT", 0.0025), 0.0025)

    for x in candidates:
        v = _safe_float(x, None) if x is not None else None
        if v is None:
            continue
        if 0.0 < float(v) < 0.10:
            return float(v)

    return float(fallback)


def _apply_stop_pct_vwap(snapshot_dict: Dict[str, Any], *, stop_pct_vwap: float) -> Dict[str, Any]:
    """
    engine側がどのキーを見ても反映される可能性を最大化するため、
    “複数の置き場所”へ同じ値を焼き付ける（安全な冗長性）。
    """
    out = dict(snapshot_dict)

    out["stop_pct_vwap"] = float(stop_pct_vwap)

    vwap = out.get("vwap") if isinstance(out.get("vwap"), dict) else {}
    vwap = dict(vwap)
    vwap["stop_pct"] = float(stop_pct_vwap)
    vwap["stop_pct_vwap"] = float(stop_pct_vwap)
    out["vwap"] = vwap

    params = out.get("params") if isinstance(out.get("params"), dict) else {}
    params = dict(params)
    pv = params.get("vwap") if isinstance(params.get("vwap"), dict) else {}
    pv = dict(pv)
    pv["stop_pct"] = float(stop_pct_vwap)
    pv["stop_pct_vwap"] = float(stop_pct_vwap)
    params["vwap"] = pv
    out["params"] = params

    return out


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
            "VWAP": w_dict.get("VWAP") if isinstance(w_dict.get("VWAP"), dict) else {},
            "BREAKOUT": w_dict.get("BREAKOUT") if isinstance(w_dict.get("BREAKOUT"), dict) else {},
        }

    return out


def _aggregate_score(metrics_by_window: Dict[int, Dict[str, Dict[str, Any]]], *, windows: List[int]) -> Dict[str, Any]:
    """
    Z準拠：PF/DD/損益/回数の“4本柱”をまとめて比較しやすい形にする。
    ここでは「VWAPの20/60を主軸」にして baseline vs candidate の差分を評価する。
    """
    pnl = 0
    dd_pct = 0.0
    pf = 0.0
    trades = 0

    for w in windows:
        m = ((metrics_by_window.get(int(w)) or {}).get("VWAP") or {})
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


def _is_improvement(base: Dict[str, Any], cand: Dict[str, Any]) -> bool:
    """
    改善条件（最小・安全寄り）
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

    if (c_dd - b_dd) > 0.003:
        return False

    if c_pf + 1e-9 < b_pf:
        return False

    if c_pnl > b_pnl:
        return True

    if c_pnl == b_pnl and (c_pf > b_pf) and (c_dd <= b_dd + 1e-9):
        return True

    return False


def _pick_knob_and_candidates(state: AutoTradeDailyState, *, active_snapshot: AutoTradeSettingSnapshot) -> List[Dict[str, Any]]:
    """
    1日1ノブだけ動かす。
    """
    gate = _get_gate_bundle_from_state(state)
    lv_v = str((gate.get("vwap") or {}).get("gate_level") or "STOP")
    lv_b = str((gate.get("breakout") or {}).get("gate_level") or "STOP")

    rr_b, rr_v = _get_rr_from_state(state)

    rr_step = float(getattr(settings, "AUTOTRADE_TUNE_RR_STEP", 0.1))
    rr_min = float(getattr(settings, "AUTOTRADE_TUNE_RR_MIN", 1.0))
    rr_max = float(getattr(settings, "AUTOTRADE_TUNE_RR_MAX", 3.0))

    def clamp_rr(x: float) -> float:
        return max(rr_min, min(rr_max, float(x)))

    stop_step = float(getattr(settings, "AUTOTRADE_TUNE_STOP_PCT_STEP", 0.0003))  # 0.03%
    stop_min = float(getattr(settings, "AUTOTRADE_TUNE_STOP_PCT_MIN", 0.0010))    # 0.10%
    stop_max = float(getattr(settings, "AUTOTRADE_TUNE_STOP_PCT_MAX", 0.0100))    # 1.00%

    def clamp_stop(x: float) -> float:
        return max(stop_min, min(stop_max, float(x)))

    cands: List[Dict[str, Any]] = []

    if lv_v == "STOP":
        snap_dict = active_snapshot.snapshot if isinstance(active_snapshot.snapshot, dict) else {}
        cur_stop = _get_stop_pct_vwap_from_snapshot(snap_dict)
        new_stop = clamp_stop(cur_stop - stop_step)
        if abs(new_stop - cur_stop) < 1e-12:
            return []

        cands.append({
            "knob": "stop_pct_vwap",
            "rr_breakout": rr_b,
            "rr_vwap": rr_v,
            "stop_pct_vwap": float(new_stop),
            "delta": float(new_stop - cur_stop),
        })
        return cands

    if lv_b != "FULL":
        cands.append({"knob": "rr_breakout", "rr_breakout": clamp_rr(rr_b - rr_step), "rr_vwap": rr_v, "delta": -rr_step})
        cands.append({"knob": "rr_breakout", "rr_breakout": clamp_rr(rr_b + rr_step), "rr_vwap": rr_v, "delta": +rr_step})
        return cands

    return []


@transaction.atomic
def auto_tune_generate_candidate(
    *,
    target_date: Optional[dt_date] = None,
    windows: Optional[List[int]] = None,
) -> AutoTuneResult:
    """
    朝フローで呼ぶ入口。
    """
    if target_date is None:
        target_date = timezone.localdate()

    if windows is None:
        windows = list(getattr(settings, "AUTOTRADE_BT_WINDOWS", [20, 60]))

    state, _ = AutoTradeDailyState.objects.get_or_create(date=target_date)
    if is_emergency_stopped(state):
        return AutoTuneResult(
            ok=True, skipped=True, reason="emergency_stop",
            created_candidate_id=None, knob=None, base={}, cand={}
        )

    active = (
        AutoTradeSettingSnapshot.objects
        .filter(status="ACTIVE")
        .order_by("-created_at")
        .first()
    )
    if not active:
        return AutoTuneResult(
            ok=False, skipped=True, reason="no_active_snapshot",
            created_candidate_id=None, knob=None, base={}, cand={}
        )

    # =========================================================
    # ★ 重要修正：同日の再作成を防ぐ（status問わず）
    #   - RETIREDになった瞬間に“今日の候補なし扱い”になる穴を塞ぐ
    # =========================================================
    exists_today_any = AutoTradeSettingSnapshot.objects.filter(
        snapshot__tune__target_date=str(target_date),
    ).exists()
    if exists_today_any:
        return AutoTuneResult(
            ok=True, skipped=True, reason="already_tuned_today",
            created_candidate_id=None, knob=None, base={}, cand={}
        )

    uni = state.universe if isinstance(state.universe, dict) else {}
    picks = [x.get("ticker") for x in (uni.get("picks") or []) if isinstance(x, dict) and x.get("ticker")]
    picks = [str(x).strip() for x in (picks or []) if str(x).strip()]

    if not picks:
        return AutoTuneResult(
            ok=True, skipped=True, reason="no_picks",
            created_candidate_id=None, knob=None, base={}, cand={}
        )

    gate_bundle = _get_gate_bundle_from_state(state)
    base_metrics = _get_baseline_metrics_from_state(state, windows=list(windows))
    base_score = _aggregate_score(base_metrics, windows=list(windows))
    rr_b0, rr_v0 = _get_rr_from_state(state)

    base_pack = {
        "gate_level": str(gate_bundle.get("final_level") or state.gate_level or "STOP"),
        "score": base_score,
        "rr": {"BREAKOUT": rr_b0, "VWAP": rr_v0},
    }

    knobs = _pick_knob_and_candidates(state, active_snapshot=active)
    if not knobs:
        return AutoTuneResult(
            ok=True, skipped=True, reason="no_tunable_knob_today",
            created_candidate_id=None, knob=None, base=base_pack, cand={}
        )

    for k in knobs:
        knob = str(k.get("knob") or "")
        rr_b = float(k.get("rr_breakout"))
        rr_v = float(k.get("rr_vwap"))
        stop_vwap = k.get("stop_pct_vwap", None)

        snap_dict = active.snapshot if isinstance(active.snapshot, dict) else {}
        new_snap = dict(snap_dict)

        if knob == "stop_pct_vwap":
            new_snap = _apply_stop_pct_vwap(new_snap, stop_pct_vwap=float(stop_vwap))

        new_snap["tune"] = {
            "target_date": str(target_date),
            "knob": knob,
            "delta": float(k.get("delta") or 0.0),
            "rr_breakout": float(rr_b),
            "rr_vwap": float(rr_v),
            "stop_pct_vwap": (float(stop_vwap) if stop_vwap is not None else None),
            "based_on_active_id": int(active.id),
            "note": "auto_tune_generate_candidate",
        }

        label = f"AUTO_TUNE {target_date} {knob} ({float(k.get('delta') or 0.0):+.6f})"
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
            rr_vwap=float(rr_v),
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
                "VWAP": d.get("VWAP") if isinstance(d.get("VWAP"), dict) else {},
                "BREAKOUT": d.get("BREAKOUT") if isinstance(d.get("BREAKOUT"), dict) else {},
            }

        cand_score = _aggregate_score(norm, windows=list(windows))

        gate = (res.get("gate") or {}) if isinstance(res, dict) else {}
        final_level = str(gate.get("final") or "STOP")

        cand_pack = {
            "gate_level": final_level,
            "score": cand_score,
            "rr": {"BREAKOUT": rr_b, "VWAP": rr_v},
            "stop_pct_vwap": (float(stop_vwap) if stop_vwap is not None else None),
        }

        if _is_improvement(base_pack, cand_pack):
            return AutoTuneResult(
                ok=True, skipped=False, reason="candidate_created",
                created_candidate_id=int(cand.id), knob=knob, base=base_pack, cand=cand_pack
            )

        # =========================================================
        # ★ 重要改善：落第でも理由を snapshot に焼き付ける
        # =========================================================
        snap_now = cand.snapshot if isinstance(cand.snapshot, dict) else {}
        snap_now["tune_rejected"] = True
        snap_now["tune_eval"] = {
            "evaluated_at": timezone.localtime(timezone.now()).isoformat(),
            "base": base_pack,
            "candidate": cand_pack,
            "note": "rejected_by_is_improvement",
        }
        cand.status = "RETIRED"
        cand.snapshot = snap_now
        cand.save(update_fields=["status", "snapshot"])

    return AutoTuneResult(
        ok=True, skipped=True, reason="no_improving_candidate",
        created_candidate_id=None, knob=None, base=base_pack, cand={}
    )