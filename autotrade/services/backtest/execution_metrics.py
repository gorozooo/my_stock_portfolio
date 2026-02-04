"""
[FILE] autotrade/services/backtest/execution_metrics.py
[PATH] <project_root>/autotrade/services/backtest/execution_metrics.py

このファイルは何？
- AutoTradeExecution（事実ログ）から、
  バックテスト評価用の「数値指標」を計算する集計サービスです。

設計原則：
- DBに保存されている「事実」だけを読む
- PF / DD / 勝率などの評価値はここで算出
- gate / UI / job はこの結果を使うだけ
"""

from __future__ import annotations

from typing import Dict, Any, List, Tuple
from django.db.models import QuerySet

from autotrade.models_backtest import AutoTradeExecution


# =========================================================
# 基本指標計算
# =========================================================
def _profit_factor(pnls: List[int]) -> float:
    wins = sum(p for p in pnls if p > 0)
    losses = abs(sum(p for p in pnls if p < 0))
    if losses == 0:
        return float("inf") if wins > 0 else 0.0
    return wins / losses


def _max_drawdown(equity_curve: List[int]) -> float:
    peak = equity_curve[0]
    max_dd = 0.0
    for v in equity_curve:
        if v > peak:
            peak = v
        dd = (peak - v) / peak if peak > 0 else 0.0
        if dd > max_dd:
            max_dd = dd
    return max_dd


def _win_loss_sums(pnls: List[int]) -> Tuple[int, int, int, int]:
    """
    return:
      wins_count, losses_count, sum_win_yen(>=0), sum_loss_yen(<=0)
    """
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    return (
        len(wins),
        len(losses),
        int(sum(wins)),
        int(sum(losses)),  # ここは負の値の合計（例: -12345）
    )


# =========================================================
# Execution 集計メイン
# =========================================================
def summarize_executions(
    *,
    qs: QuerySet[AutoTradeExecution],
    base_equity: int,
) -> Dict[str, Any]:
    """
    AutoTradeExecution queryset を受け取り、
    gate 判定に使える集計指標を返す。

    戻り値（例）：
    {
      "trades": 25,
      "win_rate": 0.52,
      "profit_factor": 1.18,
      "max_drawdown_pct": 0.032,
      "max_drawdown_yen": 32000,
      "total_pnl": 128000,

      "wins": 13,
      "losses": 12,
      "sum_win_yen": 210000,
      "sum_loss_yen": -178000,

      "min_equity": 980000,
      "max_equity": 1120000,
    }
    """

    trades = qs.count()
    if trades == 0:
        return {
            "trades": 0,
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "max_drawdown_pct": 1.0,
            "max_drawdown_yen": int(base_equity),
            "total_pnl": 0,

            "wins": 0,
            "losses": 0,
            "sum_win_yen": 0,
            "sum_loss_yen": 0,

            "min_equity": int(base_equity),
            "max_equity": int(base_equity),
        }

    pnls: List[int] = []
    equity = int(base_equity)
    equity_curve: List[int] = [equity]

    for e in qs.order_by("exit_at"):
        pnl = int(e.pnl_yen)
        pnls.append(pnl)
        equity += pnl
        equity_curve.append(equity)

    wins_count, losses_count, sum_win_yen, sum_loss_yen = _win_loss_sums(pnls)

    win_rate = (wins_count / trades) if trades > 0 else 0.0

    max_dd_pct = float(_max_drawdown(equity_curve))
    max_dd_yen = int(round(int(base_equity) * max_dd_pct))

    min_equity = int(min(equity_curve)) if equity_curve else int(base_equity)
    max_equity = int(max(equity_curve)) if equity_curve else int(base_equity)

    return {
        "trades": int(trades),
        "win_rate": round(float(win_rate), 3),
        "profit_factor": round(float(_profit_factor(pnls)), 3),
        "max_drawdown_pct": round(float(max_dd_pct), 4),
        "max_drawdown_yen": int(max_dd_yen),
        "total_pnl": int(sum(pnls)),

        # “円で読める”ための分解
        "wins": int(wins_count),
        "losses": int(losses_count),
        "sum_win_yen": int(sum_win_yen),
        "sum_loss_yen": int(sum_loss_yen),  # 負の値

        # エクイティの範囲（チェック用）
        "min_equity": int(min_equity),
        "max_equity": int(max_equity),
    }