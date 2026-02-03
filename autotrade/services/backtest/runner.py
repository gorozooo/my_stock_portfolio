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

from typing import Dict, List, Any
from datetime import date

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
from autotrade.services.backtest.engine_vwap import run_vwap


BACKTEST_WINDOWS = (20, 60, 120)
STRATEGIES = ("BREAKOUT", "VWAP")


# =========================================================
# 詳細バックテスト（Execution基準）
# =========================================================
@transaction.atomic
def run_detailed_backtests_for_universe(
    snapshot: AutoTradeSettingSnapshot,
    *,
    picks: List[str],
    target_date: date | None = None,
    force: bool = False,
) -> Dict[str, Any]:
    """
    詳細バックテストを実行し、DailyState を更新する。

    - snapshot: ACTIVE Snapshot（必須）
    - picks: 今日の銘柄リスト
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

    user = snapshot.user
    base_equity = int(
        snapshot.snapshot.get(
            "base_equity_yen",
            getattr(settings, "AUTOTRADE_BASE_EQUITY_YEN", 1_000_000),
        )
    )

    # -----------------------------------------------------
    # 既存データ削除（force）
    # -----------------------------------------------------
    if force:
        AutoTradeExecution.objects.filter(
            snapshot=snapshot,
            mode="BACKTEST",
        ).delete()

        AutoTradeBacktestRunDetail.objects.filter(
            snapshot=snapshot
        ).delete()

    metrics_by_window: Dict[int, Dict[str, Any]] = {}

    # -----------------------------------------------------
    # window × strategy ごとに実行
    # -----------------------------------------------------
    for window in BACKTEST_WINDOWS:
        window_metrics_all: List[Dict[str, Any]] = []

        for strategy in STRATEGIES:
            # ---- 実行メタ作成 ----
            run_meta = AutoTradeBacktestRunDetail.objects.create(
                user=user,
                snapshot=snapshot,
                strategy=strategy,
                window_days=window,
                start_date=target_date,
                end_date=target_date,
            )

            # ---- 各銘柄で Execution 生成 ----
            for ticker in picks:
                if strategy == "BREAKOUT":
                    run_breakout(
                        ticker=ticker,
                        window_days=window,
                        snapshot=snapshot,
                        mode="BACKTEST",
                        run_meta=run_meta,
                    )
                else:
                    run_vwap(
                        ticker=ticker,
                        window_days=window,
                        snapshot=snapshot,
                        mode="BACKTEST",
                        run_meta=run_meta,
                    )

            # ---- Execution 集計 ----
            qs = AutoTradeExecution.objects.filter(
                snapshot=snapshot,
                mode="BACKTEST",
                strategy=strategy,
                created_at__date=target_date,
            )

            metrics = summarize_executions(
                qs=qs,
                base_equity=base_equity,
            )

            window_metrics_all.append(metrics)

        # ---- window 全体の代表値（strategy平均）----
        if window_metrics_all:
            metrics_by_window[window] = {
                "trades": sum(m["trades"] for m in window_metrics_all),
                "profit_factor": (
                    sum(m["profit_factor"] for m in window_metrics_all)
                    / len(window_metrics_all)
                ),
                "max_drawdown_pct": max(
                    m["max_drawdown_pct"] for m in window_metrics_all
                ),
            }
        else:
            metrics_by_window[window] = {}

    # -----------------------------------------------------
    # gate 判定
    # -----------------------------------------------------
    gate_result = judge_multi_window(metrics_by_window)

    # -----------------------------------------------------
    # DailyState 更新
    # -----------------------------------------------------
    state.gate_level = gate_result["gate_level"]
    state.gate_reason = "\n".join(gate_result["reasons"])
    state.updated_at = timezone.now()
    state.save()

    return {
        "ok": True,
        "gate": gate_result,
        "metrics": metrics_by_window,
    }