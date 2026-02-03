"""
[FILE] autotrade/services/backtest/runner.py
[PATH] <project_root>/autotrade/services/backtest/runner.py

このファイルは何？
- 詳細バックテストの「司令塔（完成版）」です。
- Snapshot（固定設定）を入力にして、
  1) 戦略別エンジンで “1トレード=1件” を生成
  2) AutoTradeBacktestRun を作成
  3) AutoTradeExecution を bulk_create
  4) metrics を算出
  5) gate で 🟢🟡🔴 判定
  6) DailyState.backtest に UI互換の形で保存
  を一気に実行します。

初心者ポイント：
- バックテストを実行したいときは、基本この関数を呼べばOK。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple
from django.utils import timezone
from django.db import transaction

from autotrade.models import AutoTradeDailyState, AutoTradeSettingSnapshot
from autotrade.models_backtest import AutoTradeBacktestRun, AutoTradeExecution

from autotrade.services.backtest.gate import gate_from_backtests
from autotrade.services.common.guards import is_emergency_stopped

from autotrade.services.backtest.engine_breakout_detail import run_breakout_detail
from autotrade.services.backtest.engine_vwap_detail import run_vwap_detail


BACKTEST_WINDOWS = (20, 60, 120)


def _profit_factor(pnls: List[int]) -> float:
    wins = sum(p for p in pnls if p > 0)
    loss = -sum(p for p in pnls if p < 0)
    if loss <= 0:
        return float("inf") if wins > 0 else 0.0
    return float(wins) / float(loss)


def _max_drawdown_pct(equity_curve: List[float]) -> float:
    peak = None
    max_dd = 0.0
    for x in equity_curve:
        if peak is None or x > peak:
            peak = x
        dd = (peak - x) / max(peak, 1e-9)
        if dd > max_dd:
            max_dd = dd
    return float(max_dd)


def _summarize_from_executions(execs: List[AutoTradeExecution], base_equity_yen: int) -> Dict[str, Any]:
    pnls = [int(x.pnl_yen) for x in execs]
    trades = len(pnls)
    total = int(sum(pnls)) if trades else 0
    wins = sum(1 for p in pnls if p > 0)
    win_rate = (wins / trades * 100.0) if trades else 0.0
    pf = _profit_factor(pnls)

    # equity curve（取引順）
    eq = float(base_equity_yen)
    curve = [eq]
    for p in pnls:
        eq += float(p)
        curve.append(eq)
    max_dd = _max_drawdown_pct(curve)

    ev = (total / trades) if trades else 0.0
    avg_rr = float(sum(float(x.rr) for x in execs) / trades) if trades else 0.0

    return {
        "total_pnl_yen": int(total),
        "trades": int(trades),
        "win_rate_pct": float(win_rate),
        "profit_factor": float(pf if pf != float("inf") else 999.0),
        "max_drawdown_pct": float(max_dd),
        "ev_per_trade_yen": float(ev),
        "avg_rr": float(avg_rr),
    }


def _as_ui_row(metrics: Dict[str, Any]) -> Dict[str, Any]:
    """
    既存UIが期待するキー（pf/max_dd/trades/pnl）を維持する。
    追加で metrics も入れる（gate判定や詳細表示用）。
    """
    return {
        "trades": int(metrics.get("trades") or 0),
        "pf": float(metrics.get("profit_factor") or 0.0),
        "max_dd": float(metrics.get("max_drawdown_pct") or 1.0),
        "pnl": float(metrics.get("total_pnl_yen") or 0.0),
        "metrics": metrics,
    }


@transaction.atomic
def run_detailed_backtests_for_universe(
    *,
    snapshot: AutoTradeSettingSnapshot,
    picks: List[str],
    windows: Tuple[int, int, int] = BACKTEST_WINDOWS,
    date=None,
    rr_breakout: float = 2.0,
    rr_vwap: float = 1.5,
    base_equity_yen: int = 1_000_000,
) -> AutoTradeDailyState:
    """
    詳細バックテスト（戦略別×window別）を全実行し、
    - AutoTradeBacktestRun 作成
    - AutoTradeExecution bulk_create
    - DailyState.backtest 更新（UI互換）
    - gate 判定まで
    を行う。

    ※ morning_prepare から呼ぶ想定
    """

    if date is None:
        date = timezone.localdate()

    state, _ = AutoTradeDailyState.objects.get_or_create(
        date=date,
        defaults={"gate_level": "STOP", "gate_reason": ""},
    )

    # 非常停止中は何もしない（ジョブルール）
    if is_emergency_stopped(state):
        return state

    user = snapshot.user
    snapshot_dict = snapshot.snapshot if isinstance(snapshot.snapshot, dict) else {}

    bt_out: Dict[str, Dict[str, Any]] = {"BREAKOUT": {}, "VWAP": {}}

    # gate用（window -> metrics）
    # gate_from_backtests は {20:{...},60:{...}} 形式も {20:{"metrics":{...}}} 形式も許容
    # ここでは後者で統一
    gate_input_by_strategy: Dict[str, Dict[int, Dict[str, Any]]] = {"BREAKOUT": {}, "VWAP": {}}

    for strategy in ("BREAKOUT", "VWAP"):
        for w in windows:
            # runメタを作成
            run_meta = AutoTradeBacktestRun.objects.create(
                user=user,
                snapshot=snapshot,
                strategy=strategy,
                window_days=int(w),
                start_date=date,   # v1: 「実データ期間の厳密な日付」は次フェーズで算出（fetcher依存）
                end_date=date,
            )

            executions_to_create: List[AutoTradeExecution] = []

            # 全銘柄を回して “詳細トレード列” を作る
            for t in picks:
                if strategy == "BREAKOUT":
                    r = run_breakout_detail(
                        snapshot_dict=snapshot_dict,
                        ticker=t,
                        window_days=int(w),
                        rr=float(rr_breakout),
                        base_equity_yen=int(base_equity_yen),
                    )
                else:
                    r = run_vwap_detail(
                        snapshot_dict=snapshot_dict,
                        ticker=t,
                        window_days=int(w),
                        rr=float(rr_vwap),
                        base_equity_yen=int(base_equity_yen),
                    )

                for x in (r.get("trades") or []):
                    executions_to_create.append(
                        AutoTradeExecution(
                            user=user,
                            mode="BACKTEST",
                            snapshot=snapshot,
                            strategy=strategy,
                            ticker=str(x["ticker"]),
                            side=str(x.get("side") or "LONG"),
                            entry_at=x["entry_at"],
                            entry_price=float(x["entry_price"]),
                            size=int(x["size"]),
                            exit_at=x["exit_at"],
                            exit_price=float(x["exit_price"]),
                            exit_reason=str(x["exit_reason"]),
                            pnl_yen=int(x["pnl_yen"]),
                            rr=float(x["rr"]),
                            holding_minutes=int(x["holding_minutes"]),
                        )
                    )

            # bulk_create（速度&一括）
            if executions_to_create:
                AutoTradeExecution.objects.bulk_create(executions_to_create, batch_size=1000)

            # このrunのexecutionを読み直してmetrics算出（確実にDBの事実に基づく）
            qs = AutoTradeExecution.objects.filter(
                user=user,
                mode="BACKTEST",
                snapshot=snapshot,
                strategy=strategy,
            ).order_by("entry_at")

            # v1では「run単位の識別」をexecution側に持ってないので、
            # ここは windowごとの実行を「run_meta作成→直後に作成したexecution」で完璧に紐付けられない。
            # ただし、現状は “詳細バックテストを完成させる” が目的なので、
            # v1は metrics をエンジン結果から算出し、execution保存は監査ログとして成立させる。
            # （次フェーズで execution に run_id(FK) を追加して完全紐付けする）
            #
            # なので v1 metrics は “今回生成した executions_to_create” から算出する。
            if executions_to_create:
                tmp_metrics = _summarize_from_executions(executions_to_create, int(base_equity_yen))
            else:
                tmp_metrics = _summarize_from_executions([], int(base_equity_yen))

            row = _as_ui_row(tmp_metrics)
            row["run_id"] = int(run_meta.id)

            bt_out[strategy][str(w)] = row
            gate_input_by_strategy[strategy][int(w)] = {"metrics": tmp_metrics}

    # -----------------------------------------------------
    # gate 判定：今日の戦略は state.strategy があればそれ、なければ BREAKOUT
    # -----------------------------------------------------
    chosen_strategy = state.strategy or "BREAKOUT"
    bt_for_gate = gate_input_by_strategy.get(chosen_strategy, {})
    gate_level, gate_reason = gate_from_backtests(bt_for_gate)

    state.gate_level = gate_level
    state.gate_reason = gate_reason

    # UI互換：そのまま backtest に保存
    state.backtest = bt_out
    state.updated_at = timezone.now()
    state.save()

    return state