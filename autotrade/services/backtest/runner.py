"""
[FILE] autotrade/services/backtest/runner.py
[PATH] <project_root>/autotrade/services/backtest/runner.py

詳細バックテストの司令塔。

役割：
- Snapshot（固定設定）を入力として
- Execution（事実ログ）を生成
- Execution を集計
- gate 判定（FULL / LIGHT / STOP）
- DailyState を更新

重要：
- 集計値は保存しない
- 事実（Execution）だけが真実
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

# エンジン（Execution を吐く / 互換で旧集計も返せる）
from autotrade.services.backtest.engine_breakout import run_breakout
from autotrade.services.backtest.engine_vwap import run_vwap


DEFAULT_BACKTEST_WINDOWS: Tuple[int, int, int] = (20, 60, 120)
STRATEGIES: Tuple[str, str] = ("BREAKOUT", "VWAP")


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
    rr_vwap: Optional[float] = None,
    base_equity_yen: Optional[int] = None,
    force: bool = False,
) -> Dict[str, Any]:
    """
    詳細バックテストを実行し、DailyState を更新する。

    - snapshot: ACTIVE Snapshot（必須）
    - picks: 今日の銘柄リスト
    - windows: 実行期間（例: (20,60,120)）
    - rr_breakout / rr_vwap: RR（設定値）
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
    bt_windows = tuple(
        int(x) for x in (
            windows or tuple(getattr(settings, "AUTOTRADE_BT_WINDOWS", DEFAULT_BACKTEST_WINDOWS))
        )
    )
    rr_b = float(rr_breakout if rr_breakout is not None else getattr(settings, "AUTOTRADE_RR_BREAKOUT", 2.0))
    rr_v = float(rr_vwap if rr_vwap is not None else getattr(settings, "AUTOTRADE_RR_VWAP", 1.5))

    snap_dict = snapshot.snapshot if isinstance(snapshot.snapshot, dict) else {}
    if base_equity_yen is not None:
        base_equity = int(base_equity_yen)
    else:
        base_equity = int(snap_dict.get("base_equity_yen", getattr(settings, "AUTOTRADE_BASE_EQUITY_YEN", 1_000_000)))

    user = snapshot.user

    # -----------------------------------------------------
    # 既存データ削除（force）
    # - run_detail ごと消す（run_detail FK が CASCADE なので executions も消える）
    # -----------------------------------------------------
    if force:
        AutoTradeBacktestRunDetail.objects.filter(
            snapshot=snapshot,
            executed_at__date=target_date,
        ).delete()

        # 追加保険：run_detail無しで紛れ込んだBACKTESTが残っても困るので日付で掃除
        AutoTradeExecution.objects.filter(
            snapshot=snapshot,
            mode="BACKTEST",
            created_at__date=target_date,
        ).delete()

    # picks が空なら何もしない（ただし状態は返す）
    if not picks:
        state.gate_level = "STOP"
        state.gate_reason = "銘柄が0件のため、詳細バックテストを実行できません。"
        state.updated_at = timezone.now()
        state.save()
        return {"ok": False, "reason": "no_picks"}

    metrics_by_window: Dict[int, Dict[str, Any]] = {}

    # -----------------------------------------------------
    # window × strategy ごとに実行
    # -----------------------------------------------------
    for window in bt_windows:
        window_metrics_all: List[Dict[str, Any]] = []

        for strategy in STRATEGIES:
            # ---- 実行メタ作成 ----
            run_meta = AutoTradeBacktestRunDetail.objects.create(
                user=user,
                snapshot=snapshot,
                strategy=str(strategy),
                window_days=int(window),
                start_date=target_date,
                end_date=target_date,
            )

            # ---- 各銘柄で Execution 生成 ----
            for ticker in picks:
                if strategy == "BREAKOUT":
                    run_breakout(
                        ticker=ticker,
                        window_days=int(window),
                        rr=rr_b,
                        snapshot=snapshot,
                        mode="BACKTEST",
                        run_meta=run_meta,
                        target_date=target_date,
                    )
                else:
                    run_vwap(
                        ticker=ticker,
                        window_days=int(window),
                        rr=rr_v,
                        snapshot=snapshot,
                        mode="BACKTEST",
                        run_meta=run_meta,
                        target_date=target_date,
                    )

            # ---- Execution 集計（この run_meta だけ）----
            qs = AutoTradeExecution.objects.filter(
                run_detail=run_meta,
            )

            metrics = summarize_executions(
                qs=qs,
                base_equity=base_equity,
            )

            window_metrics_all.append(metrics)

        # ---- window 全体の代表値（strategy合算＋PF平均＋DD最大）----
        if window_metrics_all:
            metrics_by_window[int(window)] = {
                "trades": int(sum(int(m.get("trades", 0)) for m in window_metrics_all)),
                "profit_factor": float(
                    sum(float(m.get("profit_factor", 0.0)) for m in window_metrics_all)
                    / max(len(window_metrics_all), 1)
                ),
                "max_drawdown_pct": float(max(float(m.get("max_drawdown_pct", 1.0)) for m in window_metrics_all)),
            }
        else:
            metrics_by_window[int(window)] = {}

    # -----------------------------------------------------
    # gate 判定
    # -----------------------------------------------------
    gate_result = judge_multi_window(metrics_by_window)

    # -----------------------------------------------------
    # DailyState 更新
    # -----------------------------------------------------
    state.gate_level = gate_result.get("gate_level") or "STOP"
    state.gate_reason = "\n".join(gate_result.get("reasons") or [])
    state.updated_at = timezone.now()
    state.save()

    return {
        "ok": True,
        "gate": gate_result,
        "metrics": metrics_by_window,
    }