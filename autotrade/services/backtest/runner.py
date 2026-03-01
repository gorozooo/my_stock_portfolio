"""
[FILE] autotrade/services/backtest/runner.py
[PATH] <project_root>/autotrade/services/backtest/runner.py

このファイルは何？
- 詳細バックテストの司令塔（Execution基準）。

今回の変更（超重要）：
- RR(BREAKOUT) は settings ではなく、ACTIVE Snapshot の中身を最優先にする
- rr_breakout 引数は “指定があっても最後の保険” に落とす（= 事故らないため）
"""

from __future__ import annotations

from typing import Dict, List, Any, Optional, Tuple
from datetime import date as dt_date

from django.db import transaction
from django.utils import timezone
from django.conf import settings

from autotrade.models import AutoTradeDailyState, AutoTradeSettingSnapshot
from autotrade.models_backtest import (
    AutoTradeExecution,
    AutoTradeBacktestRunDetail,
)

from autotrade.services.backtest.execution_metrics import summarize_executions
from autotrade.services.backtest.gate import judge_multi_window
from autotrade.services.common.guards import is_emergency_stopped

# エンジン（Execution を吐く）
from autotrade.services.backtest.engine_breakout import run_breakout


DEFAULT_BACKTEST_WINDOWS: Tuple[int, int, int] = (20, 60, 120)
STRATEGIES: Tuple[str, ...] = ("BREAKOUT",)


def _safe_float(x: Any, default: float = 0.0) -> float:
    try:
        return float(x)
    except Exception:
        return float(default)


def _get_rr_breakout_from_snapshot(snapshot: AutoTradeSettingSnapshot) -> Optional[float]:
    """
    ★唯一の真実：snapshot内のRRを読む
    優先：
      1) snapshot.snapshot['lab']['BREAKOUT']['rr']   （UI由来・現状の実態に合わせる）
      2) snapshot.snapshot['tune']['rr_breakout']     （保険）
    """
    try:
        sdict = snapshot.snapshot if isinstance(snapshot.snapshot, dict) else {}
    except Exception:
        sdict = {}

    lab = sdict.get("lab") if isinstance(sdict.get("lab"), dict) else {}
    bo = lab.get("BREAKOUT") if isinstance(lab.get("BREAKOUT"), dict) else {}
    rr1 = bo.get("rr", None)
    if rr1 is not None:
        return _safe_float(rr1, None)

    tune = sdict.get("tune") if isinstance(sdict.get("tune"), dict) else {}
    rr2 = tune.get("rr_breakout", None)
    if rr2 is not None:
        return _safe_float(rr2, None)

    return None


def _build_final_gate(
    *,
    gate_breakout: Dict[str, Any],
    rr_breakout: float,
    windows: Tuple[int, ...],
) -> Dict[str, Any]:
    """
    BREAKOUT一本運用の最終ゲートを組み立てる。
    - gate.py の reasons（日本語＋数値＋円）を尊重し、そのまま返す。
    """
    lv = str((gate_breakout or {}).get("gate_level") or "STOP")

    if lv == "FULL":
        final = "FULL"
        active = ["BREAKOUT"]
        disabled: List[str] = []
        head = "【最終判定】FULL（BREAKOUT 稼働）"
    elif lv == "LIGHT":
        final = "LIGHT"
        active = ["BREAKOUT"]
        disabled = []
        head = "【最終判定】LIGHT（BREAKOUT 軽稼働）"
    else:
        final = "STOP"
        active = []
        disabled = ["BREAKOUT"]
        head = "【最終判定】STOP（安全のため稼働しない）"

    reasons: List[str] = [head]
    reasons.append(f"【設定】windows={list(windows)} / RR(BREAKOUT)={rr_breakout:.2f}")

    rs = (gate_breakout or {}).get("reasons") or []
    if rs:
        reasons.append("【BREAKOUT判定】")
        reasons.extend([str(x) for x in rs if str(x).strip()])

    return {
        "gate_level": final,
        "reasons": reasons,
        "active_strategies": active,
        "disabled_strategies": disabled,
        "gate_breakout": gate_breakout,
    }


# =========================================================
# 詳細バックテスト（Execution基準）
# =========================================================
@transaction.atomic
def run_detailed_backtests_for_universe(
    *,
    snapshot: AutoTradeSettingSnapshot,
    picks: List[str],
    target_date: Optional[dt_date] = None,
    windows: Optional[Tuple[int, ...]] = None,
    rr_breakout: Optional[float] = None,
    base_equity_yen: Optional[int] = None,
    force: bool = False,
) -> Dict[str, Any]:
    """
    詳細バックテストを実行し、DailyState を更新する（BREAKOUTのみ）。

    - snapshot: ACTIVE Snapshot（必須）
    - picks: 今日の銘柄リスト
    - windows: 実行期間（例: (20,60,120)）
    - rr_breakout: 互換用（原則使わない。snapshot内が唯一の真実）
    - base_equity_yen: 基準資産（Snapshot優先、なければsettings）
    - force: True の場合、既存 Execution を削除して再実行
    """

    if target_date is None:
        target_date = timezone.localdate()

    state, _ = AutoTradeDailyState.objects.get_or_create(date=target_date)

    # -----------------------------------------------------
    # 非常停止ガード
    # -----------------------------------------------------
    if is_emergency_stopped(state):
        return {"skipped": True, "reason": "emergency_stop"}

    if snapshot is None:
        raise ValueError("ACTIVE snapshot が存在しません。")

    # -----------------------------------------------------
    # 入力の正規化
    # -----------------------------------------------------
    picks = [str(x).strip() for x in (picks or []) if str(x).strip()]
    bt_windows = tuple(int(x) for x in (windows or tuple(getattr(settings, "AUTOTRADE_BT_WINDOWS", DEFAULT_BACKTEST_WINDOWS))))

    # ★ RR(BREAKOUT) は snapshot 内を最優先（唯一の真実）
    rr_from_snap = _get_rr_breakout_from_snapshot(snapshot)
    if rr_from_snap is not None:
        rr_b = float(rr_from_snap)
    elif rr_breakout is not None:
        # 互換用の保険
        rr_b = float(rr_breakout)
    else:
        rr_b = float(getattr(settings, "AUTOTRADE_RR_BREAKOUT", 2.0))

    snap_dict = snapshot.snapshot if isinstance(snapshot.snapshot, dict) else {}
    if base_equity_yen is not None:
        base_equity = int(base_equity_yen)
    else:
        base_equity = int(snap_dict.get("base_equity_yen", getattr(settings, "AUTOTRADE_BASE_EQUITY_YEN", 1_000_000)))

    user = snapshot.user

    # -----------------------------------------------------
    # 既存データ削除（force）
    #   - trade_date基準で掃除（UTC/JST混線を根絶）
    # -----------------------------------------------------
    if force:
        details = AutoTradeBacktestRunDetail.objects.filter(
            snapshot=snapshot,
            trade_date=target_date,
        )
        AutoTradeExecution.objects.filter(run_detail__in=details).delete()
        details.delete()

    # picks が空なら何もしない（ただし状態は残す）
    if not picks:
        state.gate_level = "STOP"
        state.gate_reason = "銘柄が0件のため、詳細バックテストを実行できません。"
        state.strategy = ""
        state.strategy_decision = {
            "mode": "STOP",
            "active": [],
            "disabled": ["BREAKOUT"],
            "note": "no_picks",
        }
        state.backtest = {
            "meta": {"date": str(target_date), "note": "no_picks"},
            "by_window": {},
            "gate": {},
        }
        state.updated_at = timezone.now()
        state.save()
        return {"ok": False, "reason": "no_picks"}

    metrics_by_window_strategy: Dict[int, Dict[str, Dict[str, Any]]] = {}

    for window in bt_windows:
        metrics_by_window_strategy[int(window)] = {}

        # -------------------------------------------------
        # 既存 run_detail を再利用（同日再実行で暴増殖を防ぐ）
        # -------------------------------------------------
        run_detail = AutoTradeBacktestRunDetail.objects.filter(
            snapshot=snapshot,
            strategy="BREAKOUT",
            window_days=int(window),
            trade_date=target_date,
        ).order_by("-id").first()

        if (run_detail is None) or force:
            run_detail = AutoTradeBacktestRunDetail.objects.create(
                user=user,
                snapshot=snapshot,
                strategy="BREAKOUT",
                window_days=int(window),
                trade_date=target_date,
                start_date=target_date,
                end_date=target_date,
            )

        # -------------------------------------------------
        # 既に Execution があるなら（force=False）再生成しない
        # -------------------------------------------------
        exists_exec = AutoTradeExecution.objects.filter(run_detail=run_detail).exists()
        if (not exists_exec) or force:
            if force and exists_exec:
                AutoTradeExecution.objects.filter(run_detail=run_detail).delete()

            for ticker in picks:
                run_breakout(
                    ticker=ticker,
                    window_days=int(window),
                    rr=rr_b,
                    snapshot=snapshot,
                    mode="BACKTEST",
                    run_meta=run_detail,
                    target_date=target_date,
                )

        qs = AutoTradeExecution.objects.filter(run_detail=run_detail).order_by("exit_at")
        metrics = summarize_executions(qs=qs, base_equity=base_equity)
        metrics_by_window_strategy[int(window)]["BREAKOUT"] = metrics

    metrics_breakout: Dict[int, Dict[str, Any]] = {int(w): (metrics_by_window_strategy.get(int(w)) or {}).get("BREAKOUT") or {} for w in bt_windows}
    gate_breakout = judge_multi_window(metrics_breakout)

    merged = _build_final_gate(
        gate_breakout=gate_breakout,
        rr_breakout=rr_b,
        windows=bt_windows,
    )

    final_level = str(merged.get("gate_level") or "STOP")
    active = list(merged.get("active_strategies") or [])
    disabled = list(merged.get("disabled_strategies") or [])

    # strategy表示はBREAKOUT固定（停止時は空でOK）
    if final_level in ["FULL", "LIGHT"]:
        state.strategy = "BREAKOUT"
    else:
        state.strategy = ""

    state.gate_level = final_level
    state.gate_reason = "\n".join([str(x) for x in (merged.get("reasons") or []) if str(x).strip()])
    state.strategy_decided_at = timezone.now()
    state.strategy_decision = {
        "mode": final_level,
        "active": active,
        "disabled": disabled,
        "rr": {"BREAKOUT": rr_b},
        "windows": list(bt_windows),
    }

    state.backtest = {
        "meta": {
            "date": str(target_date),
            "base_equity_yen": int(base_equity),
            "rr_breakout": float(rr_b),
            "windows": list(bt_windows),
        },
        "by_window": metrics_by_window_strategy,
        "gate": {
            "final": {"gate_level": final_level, "active": active, "disabled": disabled},
            "BREAKOUT": gate_breakout,
        },
    }

    state.updated_at = timezone.now()
    state.save()

    return {
        "ok": True,
        "gate": {
            "final": final_level,
            "breakout": gate_breakout,
            "active": active,
            "disabled": disabled,
        },
        "metrics": metrics_by_window_strategy,
    }