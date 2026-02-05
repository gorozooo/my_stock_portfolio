"""
[FILE] autotrade/services/tuning/auto_promote.py
[PATH] <project_root>/autotrade/services/tuning/auto_promote.py

何をする？
- CANDIDATE（本番候補）のSnapshotを「今日のExecution（事実ログ）」で評価し、
  条件を満たすものがあれば自動でACTIVEへ昇格させる。

重要ルール（固定）：
- OKボタン不要で自動昇格（ユーザー要求）
- ACTIVEは上書きしない（世代交代）：旧ACTIVEはRETIREDへ
- 昇格の根拠（evidence）を snapshot JSON に焼き付ける（再現性・説明責任）
- emergency_stop の日は昇格しない（安全弁）
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple
from datetime import date as dt_date

from django.db import transaction
from django.utils import timezone

from autotrade.models import AutoTradeDailyState, AutoTradeSettingSnapshot
from autotrade.models_backtest import AutoTradeBacktestRunDetail, AutoTradeExecution
from autotrade.services.common.guards import is_emergency_stopped
from autotrade.services.backtest.execution_metrics import summarize_executions
from autotrade.services.backtest.gate import judge_multi_window


STRATEGIES: Tuple[str, str] = ("VWAP", "BREAKOUT")


def _merge_gate_results(*, gate_vwap: Dict[str, Any], gate_breakout: Dict[str, Any]) -> Dict[str, Any]:
    """
    runner.py の統合ルールと同じ思想で最終判定を作る（本番の意思決定と揃える）

    FULL条件（あなたの方針：最初からFULL狙い）
    - VWAP が STOP ではない（LIGHT/FULL）
    - かつ BREAKOUT が FULL
    """
    lv_v = str((gate_vwap or {}).get("gate_level") or "STOP")
    lv_b = str((gate_breakout or {}).get("gate_level") or "STOP")

    active: List[str] = []
    disabled: List[str] = []

    if lv_v == "STOP":
        final = "STOP"
        disabled = ["VWAP", "BREAKOUT"]
    else:
        active.append("VWAP")
        if lv_b == "FULL":
            final = "FULL"
            active.append("BREAKOUT")
        else:
            final = "LIGHT"
            disabled.append("BREAKOUT")

    return {
        "gate_level": final,
        "active": active,
        "disabled": disabled,
        "gate_vwap": gate_vwap,
        "gate_breakout": gate_breakout,
    }


def _collect_metrics_for_snapshot(
    *,
    snapshot: AutoTradeSettingSnapshot,
    target_date: dt_date,
    windows: List[int],
) -> Dict[str, Any]:
    """
    ある snapshot について、今日の run_detail/Execution を読み取り、
    window×strategy の metrics を作って gate 判定まで返す。
    """
    by_window: Dict[int, Dict[str, Dict[str, Any]]] = {}

    # base_equity は snapshot の値を優先（無ければ100万）
    snap_dict = snapshot.snapshot if isinstance(snapshot.snapshot, dict) else {}
    base_equity = int(snap_dict.get("base_equity_yen", 1_000_000))

    for w in windows:
        w = int(w)
        by_window[w] = {}
        for strat in STRATEGIES:
            rd = (
                AutoTradeBacktestRunDetail.objects
                .filter(snapshot=snapshot, strategy=str(strat), window_days=w, executed_at__date=target_date)
                .order_by("-id")
                .first()
            )
            if not rd:
                by_window[w][strat] = {"trades": 0}
                continue

            qs = AutoTradeExecution.objects.filter(run_detail=rd).order_by("exit_at")
            m = summarize_executions(qs=qs, base_equity=base_equity)
            by_window[w][strat] = m

    # strategy別に gate 判定（gate.py をそのまま使用）
    metrics_vwap = {int(w): (by_window[int(w)].get("VWAP") or {}) for w in windows}
    metrics_breakout = {int(w): (by_window[int(w)].get("BREAKOUT") or {}) for w in windows}

    gate_vwap = judge_multi_window(metrics_vwap)
    gate_breakout = judge_multi_window(metrics_breakout)
    merged = _merge_gate_results(gate_vwap=gate_vwap, gate_breakout=gate_breakout)

    return {
        "base_equity_yen": base_equity,
        "by_window": by_window,
        "gate": merged,
    }


@transaction.atomic
def auto_promote_if_ready(
    *,
    target_date: Optional[dt_date] = None,
    windows: Optional[List[int]] = None,
) -> Dict[str, Any]:
    """
    今日のCANDIDATEを評価して、FULLなら自動昇格する。

    前提：
    - CANDIDATE snapshot は別ジョブ（オートチューニング等）で作られている想定
    - そのCANDIDATEについて、同日に run_detail + Execution が既に存在していること
      （無ければ評価できないのでスキップ）
    """
    if target_date is None:
        target_date = timezone.localdate()

    if windows is None:
        windows = [20, 60]

    # emergency_stop の日は昇格もしない（安全弁）
    state, _ = AutoTradeDailyState.objects.get_or_create(date=target_date)
    if is_emergency_stopped(state):
        return {"ok": True, "skipped": True, "reason": "emergency_stop"}

    # 現在のACTIVE
    active = (
        AutoTradeSettingSnapshot.objects
        .filter(status="ACTIVE")
        .order_by("-created_at")
        .first()
    )

    # 今日の候補（新しい順）
    candidates = (
        AutoTradeSettingSnapshot.objects
        .filter(status="CANDIDATE")
        .order_by("-created_at")
    )

    promoted: Optional[AutoTradeSettingSnapshot] = None
    promoted_eval: Optional[Dict[str, Any]] = None

    for cand in candidates:
        ev = _collect_metrics_for_snapshot(snapshot=cand, target_date=target_date, windows=list(windows))

        final = str(((ev.get("gate") or {}).get("gate_level")) or "STOP")
        # あなたの方針：最初からFULL狙い → FULLのみ昇格
        if final != "FULL":
            continue

        # ここまで来たら昇格する（最初に見つけたFULLを採用）
        promoted = cand
        promoted_eval = ev
        break

    if not promoted:
        return {"ok": True, "promoted": False, "reason": "no_full_candidate"}

    # ACTIVE世代交代（旧ACTIVEは残す）
    if active and active.id != promoted.id:
        active.status = "RETIRED"
        active.save(update_fields=["status"])

    promoted.status = "ACTIVE"

    # snapshot JSON に「昇格ログ（証拠）」を焼き付け
    snap_dict = promoted.snapshot if isinstance(promoted.snapshot, dict) else {}
    snap_dict["promote"] = {
        "promoted_at": timezone.now().isoformat(),
        "promoted_by": "AUTO",
        "windows": list(windows),
        "from_status": "CANDIDATE",
        "note": "auto_promote_if_ready",
    }
    snap_dict["evidence"] = {
        "date": str(target_date),
        "base_equity_yen": int((promoted_eval or {}).get("base_equity_yen") or 1_000_000),
        "gate": (promoted_eval or {}).get("gate") or {},
        "by_window": (promoted_eval or {}).get("by_window") or {},
    }
    promoted.snapshot = snap_dict
    promoted.save(update_fields=["status", "snapshot"])

    return {
        "ok": True,
        "promoted": True,
        "new_active_id": promoted.id,
        "old_active_id": (active.id if active else None),
        "gate": (promoted_eval or {}).get("gate"),
    }