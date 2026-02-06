"""
[FILE] autotrade/services/tuning/auto_promote.py
[PATH] <project_root>/autotrade/services/tuning/auto_promote.py

このファイルは何？
- CANDIDATE（本番候補）のSnapshotを「今日のExecution（事実ログ）」で評価し、
  条件を満たすものがあれば自動でACTIVEへ昇格させる。

重要ルール（固定）：
- OKボタン不要で自動昇格（ユーザー要求）
- ACTIVEは上書きしない（世代交代）：旧ACTIVEはRETIREDへ
- 昇格の根拠（evidence）を snapshot JSON に焼き付ける（再現性・説明責任）
- emergency_stop の日は昇格しない（安全弁）

今回の変更ポイント：
- run_detail 検索キーを executed_at__date から trade_date に変更（UTC/JST混線を根絶）
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

    snap_dict = snapshot.snapshot if isinstance(snapshot.snapshot, dict) else {}
    base_equity = int(snap_dict.get("base_equity_yen", 1_000_000))

    for w in windows:
        w = int(w)
        by_window[w] = {}
        for strat in STRATEGIES:
            rd = (
                AutoTradeBacktestRunDetail.objects
                .filter(snapshot=snapshot, strategy=str(strat), window_days=w, trade_date=target_date)
                .order_by("-id")
                .first()
            )
            if not rd:
                by_window[w][strat] = {"trades": 0}
                continue

            qs = AutoTradeExecution.objects.filter(run_detail=rd).order_by("exit_at")
            m = summarize_executions(qs=qs, base_equity=base_equity)
            by_window[w][strat] = m

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


def _write_candidate_evidence(
    *,
    cand: AutoTradeSettingSnapshot,
    target_date: dt_date,
    windows: List[int],
    ev: Dict[str, Any],
    note: str,
) -> None:
    """
    CANDIDATE の snapshot JSON に評価結果を焼き付ける。
    - FULLでなくても保存する（UIで落第理由を見るため）
    - gate.py の reasons（日本語＋円）も含まれる
    """
    snap_dict = cand.snapshot if isinstance(cand.snapshot, dict) else {}

    snap_dict["auto_eval"] = {
        "evaluated_at": timezone.localtime(timezone.now()).isoformat(),
        "date": str(target_date),
        "windows": list(windows),
        "note": str(note or ""),
        "final_gate_level": str(((ev.get("gate") or {}).get("gate_level")) or "STOP"),
    }

    snap_dict["evidence"] = {
        "date": str(target_date),
        "base_equity_yen": int(ev.get("base_equity_yen") or 1_000_000),
        "gate": ev.get("gate") or {},
        "by_window": ev.get("by_window") or {},
    }

    cand.snapshot = snap_dict
    cand.save(update_fields=["snapshot"])


@transaction.atomic
def auto_promote_if_ready(
    *,
    target_date: Optional[dt_date] = None,
    windows: Optional[List[int]] = None,
) -> Dict[str, Any]:
    """
    今日のCANDIDATEを評価して、FULLなら自動昇格する。
    """
    if target_date is None:
        target_date = timezone.localdate()

    if windows is None:
        windows = [20, 60]

    windows = [int(x) for x in (windows or [])]

    state, _ = AutoTradeDailyState.objects.get_or_create(date=target_date)
    if is_emergency_stopped(state):
        return {"ok": True, "skipped": True, "reason": "emergency_stop"}

    active = (
        AutoTradeSettingSnapshot.objects
        .filter(status="ACTIVE")
        .order_by("-created_at")
        .first()
    )

    candidates = (
        AutoTradeSettingSnapshot.objects
        .filter(status="CANDIDATE")
        .order_by("-created_at")
    )

    promoted: Optional[AutoTradeSettingSnapshot] = None
    promoted_eval: Optional[Dict[str, Any]] = None

    evaluated: List[Dict[str, Any]] = []

    for cand in candidates:
        ev = _collect_metrics_for_snapshot(snapshot=cand, target_date=target_date, windows=list(windows))

        final = str(((ev.get("gate") or {}).get("gate_level")) or "STOP")
        evaluated.append({"id": cand.id, "label": cand.label, "final_gate_level": final})

        _write_candidate_evidence(
            cand=cand,
            target_date=target_date,
            windows=list(windows),
            ev=ev,
            note="auto_promote_if_ready",
        )

        if final != "FULL":
            continue

        promoted = cand
        promoted_eval = ev
        break

    if not promoted:
        return {"ok": True, "promoted": False, "reason": "no_full_candidate", "evaluated": evaluated}

    if active and active.id != promoted.id:
        active.status = "RETIRED"
        active.save(update_fields=["status"])

    promoted.status = "ACTIVE"

    snap_dict = promoted.snapshot if isinstance(promoted.snapshot, dict) else {}
    snap_dict["promote"] = {
        "promoted_at": timezone.localtime(timezone.now()).isoformat(),
        "promoted_by": "AUTO",
        "windows": list(windows),
        "from_status": "CANDIDATE",
        "note": "auto_promote_if_ready",
    }
    promoted.snapshot = snap_dict
    promoted.save(update_fields=["status", "snapshot"])

    return {
        "ok": True,
        "promoted": True,
        "new_active_id": promoted.id,
        "old_active_id": (active.id if active else None),
        "gate": (promoted_eval or {}).get("gate"),
        "evaluated": evaluated,
    }