"""
[FILE] autotrade/services/tuning/auto_tune.py
[PATH] <project_root>/autotrade/services/tuning/auto_tune.py

このファイルは何？
- 自動チューニング（最小構成）の本体です。
- 毎朝1ノブだけ動かし、候補Snapshot（CANDIDATE）を作って同日Executionで検証します。
- 改善した候補だけ残し、悪化は即RETIREDにします（暴走防止）。
- 落第した候補にも tune_eval（比較と理由）を焼き付け、UIで見える化します。

今回の変更（BREAKOUT一本運用）：
- 評価・比較・改善判定は BREAKOUT のみで行う
- ノブは最小のまま：rr_breakout を ±step で探索（1日1ノブ）
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


def _get_breakout_gate_from_state(state: AutoTradeDailyState) -> Dict[str, Any]:
    """
    runner.py が作った gate を読む。
    BREAKOUT一本運用なので、最終判定はBREAKOUTに寄せる。
    """
    bt = state.backtest if isinstance(state.backtest, dict) else {}
    gate = bt.get("gate") if isinstance(bt.get("gate"), dict) else {}
    final = gate.get("final") if isinstance(gate.get("final"), dict) else {}

    # runner側がfinalを作っていれば尊重（ただし無ければBREAKOUTを採用）
    final_level = final.get("gate_level")
    if final_level is None:
        bo = gate.get("BREAKOUT") if isinstance(gate.get("BREAKOUT"), dict) else {}
        final_level = bo.get("gate_level")

    bo_gate = gate.get("BREAKOUT") if isinstance(gate.get("BREAKOUT"), dict) else {}

    return {
        "final_level": str(final_level or state.gate_level or "STOP"),
        "breakout": bo_gate,
    }


def _get_rr_breakout_from_state(state: AutoTradeDailyState) -> float:
    """
    rrは state.backtest['meta'] を優先。無ければsettings。
    """
    bt = state.backtest if isinstance(state.backtest, dict) else {}
    meta = bt.get("meta") if isinstance(bt.get("meta"), dict) else {}
    rr_b = _safe_float(meta.get("rr_breakout"), _safe_float(getattr(settings, "AUTOTRADE_RR_BREAKOUT", 2.0), 2.0))
    return float(rr_b)


def _get_baseline_metrics_from_state(state: AutoTradeDailyState, *, windows: List[int]) -> Dict[str, Any]:
    """
    baseline は、朝の runner が既に作った state.backtest['by_window'] を読む（再計算しない）。
    """
    bt = state.backtest if isinstance(state.backtest, dict) else {}
    by_window = bt.get("by_window") if isinstance(bt.get("by_window"), dict) else {}

    # by_window は {20: {"BREAKOUT": {...}}} か、
    # JSONField経由でキーが文字列になるケースもあるので両対応。
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
    PF/DD/損益/回数の“4本柱”をまとめて比較しやすい形にする。
    BREAKOUT一本運用なので、BREAKOUTの20/60（など）で集計する。
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
    UIに出すための「比較理由」を文字列でまとめる。
    ※ gate.py の理由文（日本語＋円）とは役割が違う（これは tuning の比較ロジックの説明）
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

    # gate差
    if _GATE_RANK.get(c_gate, 0) > _GATE_RANK.get(b_gate, 0):
        reasons.append(f"gate改善：{b_gate} → {c_gate}")
    elif _GATE_RANK.get(c_gate, 0) < _GATE_RANK.get(b_gate, 0):
        reasons.append(f"gate悪化：{b_gate} → {c_gate}")

    # trades激減
    if b_tr > 0 and c_tr < int(b_tr * 0.70):
        reasons.append(f"取引回数が減りすぎ：{b_tr} → {c_tr}（70%未満）")

    # DD悪化（+0.3%超）
    if (c_dd - b_dd) > 0.003:
        reasons.append(f"最大DDが悪化：{b_dd:.4f} → {c_dd:.4f}（+0.003超）")

    # PF悪化
    if c_pf + 1e-9 < b_pf:
        reasons.append(f"PFが悪化：{b_pf:.3f} → {c_pf:.3f}")

    # PnL
    if c_pnl > b_pnl:
        reasons.append(f"損益が改善：{b_pnl}円 → {c_pnl}円")
    elif c_pnl < b_pnl:
        reasons.append(f"損益が悪化：{b_pnl}円 → {c_pnl}円")
    else:
        reasons.append(f"損益が同じ：{b_pnl}円 → {c_pnl}円")

    return {
        "evaluated_at": timezone.localtime(timezone.now()).isoformat(),
        "base": {
            "gate_level": b_gate,
            "score": {"pnl_sum_yen": b_pnl, "max_dd_pct": b_dd, "pf_avg": b_pf, "trades_sum": b_tr},
            "rr": base.get("rr") or {},
        },
        "cand": {
            "gate_level": c_gate,
            "score": {"pnl_sum_yen": c_pnl, "max_dd_pct": c_dd, "pf_avg": c_pf, "trades_sum": c_tr},
            "rr": cand.get("rr") or {},
        },
        "reasons": reasons,
    }


def _is_improvement(base: Dict[str, Any], cand: Dict[str, Any]) -> bool:
    """
    改善条件（最小・安全寄り）
    - gateが上がる（STOP→LIGHT、LIGHT→FULL）なら即OK
    - gateが同じなら：
      * 損益↑ を基本
      * DD悪化しない（+0.3%以内）
      * PF悪化しない
      * tradesが極端に減らない（ベースの70%未満はNG）
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


def _pick_knob_and_candidates(state: AutoTradeDailyState) -> List[Dict[str, Any]]:
    """
    1日1ノブだけ動かす（最小構成）。
    BREAKOUT一本運用なので、rr_breakout を微調整（±step）だけを行う。

    - rr_breakout は ±step（安全方向が一意でないため）
    """
    rr_b = _get_rr_breakout_from_state(state)

    rr_step = float(getattr(settings, "AUTOTRADE_TUNE_RR_STEP", 0.1))
    rr_min = float(getattr(settings, "AUTOTRADE_TUNE_RR_MIN", 1.0))
    rr_max = float(getattr(settings, "AUTOTRADE_TUNE_RR_MAX", 3.0))

    def clamp_rr(x: float) -> float:
        return max(rr_min, min(rr_max, float(x)))

    return [
        {"knob": "rr_breakout", "rr_breakout": clamp_rr(rr_b - rr_step), "delta": -rr_step},
        {"knob": "rr_breakout", "rr_breakout": clamp_rr(rr_b + rr_step), "delta": +rr_step},
    ]


@transaction.atomic
def auto_tune_generate_candidate(
    *,
    target_date: Optional[dt_date] = None,
    windows: Optional[List[int]] = None,
) -> AutoTuneResult:
    """
    朝フローで呼ぶ入口。
    - 今日の state（朝の runner 実行後）を前提に、候補を作って検証する。
    - 良い候補があれば CANDIDATE Snapshot を1つだけ作る（最初の合格を採用）。

    注意：
    - 既に今日 “tune.target_date が存在する” 場合は増殖させない（暴走防止）。
      created_at__date はUTC/JST混線し得るので、tune.target_date を正とする。
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

    # ACTIVE Snapshot
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

    # 今日のtuneが既にあるなら作らない（最小）
    exists_today_any = AutoTradeSettingSnapshot.objects.filter(
        snapshot__tune__target_date=str(target_date),
    ).exists()
    if exists_today_any:
        return AutoTuneResult(
            ok=True, skipped=True, reason="tune_already_exists_today",
            created_candidate_id=None, knob=None, base={}, cand={}
        )

    # picks（朝universe）
    uni = state.universe if isinstance(state.universe, dict) else {}
    picks = [x.get("ticker") for x in (uni.get("picks") or []) if isinstance(x, dict) and x.get("ticker")]
    picks = [str(x).strip() for x in (picks or []) if str(x).strip()]

    if not picks:
        return AutoTuneResult(
            ok=True, skipped=True, reason="no_picks",
            created_candidate_id=None, knob=None, base={}, cand={}
        )

    # baseline（朝runner結果を読む）
    gate_bundle = _get_breakout_gate_from_state(state)
    base_metrics = _get_baseline_metrics_from_state(state, windows=list(windows))
    base_score = _aggregate_score(base_metrics, windows=list(windows))
    rr_b0 = _get_rr_breakout_from_state(state)

    base_pack = {
        "gate_level": str(gate_bundle.get("final_level") or state.gate_level or "STOP"),
        "score": base_score,
        "rr": {"BREAKOUT": rr_b0},
    }

    # 1ノブ候補を作る
    knobs = _pick_knob_and_candidates(state)
    if not knobs:
        return AutoTuneResult(
            ok=True, skipped=True, reason="no_tunable_knob_today",
            created_candidate_id=None, knob=None, base=base_pack, cand={}
        )

    # 候補を評価して、最初の合格だけ採用
    for k in knobs:
        knob = str(k.get("knob") or "")
        rr_b = float(k.get("rr_breakout"))

        # candidate snapshot 作成（中身はACTIVEをコピー + tune情報を焼き込み）
        snap_dict = active.snapshot if isinstance(active.snapshot, dict) else {}
        new_snap = dict(snap_dict)

        new_snap["tune"] = {
            "target_date": str(target_date),
            "knob": knob,
            "delta": float(k.get("delta") or 0.0),
            "rr_breakout": float(rr_b),
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

        # 同日 backtest を candidate で実行（Execution生成）
        res = run_detailed_backtests_for_universe(
            snapshot=cand,
            picks=picks,
            target_date=target_date,
            windows=tuple(int(x) for x in windows),
            rr_breakout=float(rr_b),
            rr_vwap=None,  # BREAKOUT一本運用（runner側が受けても無視/NoneでOK）
            base_equity_yen=int(getattr(settings, "AUTOTRADE_BASE_EQUITY_YEN", 1_000_000)),
            force=True,
        )

        cand_metrics = (res.get("metrics") or {}) if isinstance(res, dict) else {}

        # 正規化
        norm: Dict[int, Dict[str, Dict[str, Any]]] = {}
        for w in windows:
            w_int = int(w)
            d = cand_metrics.get(w_int) if isinstance(cand_metrics.get(w_int), dict) else cand_metrics.get(str(w_int))
            d = d if isinstance(d, dict) else {}
            norm[w_int] = {
                "BREAKOUT": d.get("BREAKOUT") if isinstance(d.get("BREAKOUT"), dict) else {},
            }

        cand_score = _aggregate_score(norm, windows=list(windows))

        # gate（runnerの返しに幅があっても耐える）
        gate = (res.get("gate") or {}) if isinstance(res, dict) else {}
        final_level = None
        if isinstance(gate.get("final"), dict):
            final_level = (gate.get("final") or {}).get("gate_level")
        if final_level is None and isinstance(gate.get("BREAKOUT"), dict):
            final_level = (gate.get("BREAKOUT") or {}).get("gate_level")
        final_level = str(final_level or "STOP")

        cand_pack = {
            "gate_level": final_level,
            "score": cand_score,
            "rr": {"BREAKOUT": rr_b},
        }

        # tune_eval（比較と理由）を必ず焼く（合否どちらでも）
        snap_now = cand.snapshot if isinstance(cand.snapshot, dict) else {}
        snap_now["tune_eval"] = _build_tune_eval(base_pack, cand_pack)
        cand.snapshot = snap_now
        cand.save(update_fields=["snapshot"])

        # 改善判定
        if _is_improvement(base_pack, cand_pack):
            return AutoTuneResult(
                ok=True, skipped=False, reason="candidate_created",
                created_candidate_id=int(cand.id), knob=knob, base=base_pack, cand=cand_pack
            )

        # 不合格：作った候補は即RETIRED（ただし tune_eval は残る）
        cand.status = "RETIRED"
        snap_now = cand.snapshot if isinstance(cand.snapshot, dict) else {}
        snap_now["tune_rejected"] = True
        cand.snapshot = snap_now
        cand.save(update_fields=["status", "snapshot"])

    return AutoTuneResult(
        ok=True, skipped=True, reason="no_improving_candidate",
        created_candidate_id=None, knob=None, base=base_pack, cand={}
    )