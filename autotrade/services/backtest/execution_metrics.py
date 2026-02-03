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

from typing import Dict, Any, List
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
    """
    最大ドローダウン（0〜1）

    定義：
      DD = (peak - equity) / peak

    注意：
    - equity が 0 以下になると DD が 100%超になり得るが、
      gate の入力としては「破綻」扱いで十分なので 1.0 にクランプする。
    - 「なぜ破綻したか」は summarize_executions 側で min_equity を返して可視化する。
    """
    if not equity_curve:
        return 0.0

    peak = equity_curve[0]
    max_dd = 0.0

    for v in equity_curve:
        if v > peak:
            peak = v

        # peak が 0 以下なら、評価不能（破綻扱い）
        if peak <= 0:
            return 1.0

        dd = (peak - v) / peak

        # equity が 0 を割ると dd が 1 を超えるので、最大1.0にクランプ
        if dd > 1.0:
            dd = 1.0

        if dd > max_dd:
            max_dd = dd

        # すでに 100% ならこれ以上悪化しないので早期終了
        if max_dd >= 1.0:
            return 1.0

    return float(max_dd)


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
      "total_pnl": 128000,

      # デバッグ用（UIやgateは無視してOK）
      "min_equity": 930000,
      "max_equity": 1042000,
    }
    """

    trades = qs.count()
    if trades == 0:
        return {
            "trades": 0,
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "max_drawdown_pct": 1.0,
            "total_pnl": 0,
            "min_equity": int(base_equity),
            "max_equity": int(base_equity),
        }

    pnls: List[int] = []
    equity = int(base_equity)
    equity_curve: List[int] = [equity]

    wins = 0

    # exit_at が同値のときに順序がブレると equity 曲線が揺れるので id で安定化
    for e in qs.order_by("exit_at", "id"):
        pnl = int(e.pnl_yen or 0)
        pnls.append(pnl)

        equity += pnl
        equity_curve.append(equity)

        if pnl > 0:
            wins += 1

    win_rate = wins / trades if trades > 0 else 0.0

    min_equity = min(equity_curve) if equity_curve else equity
    max_equity = max(equity_curve) if equity_curve else equity

    return {
        "trades": int(trades),
        "win_rate": round(float(win_rate), 3),
        "profit_factor": round(float(_profit_factor(pnls)), 3),
        "max_drawdown_pct": round(float(_max_drawdown(equity_curve)), 4),
        "total_pnl": int(sum(pnls)),
        "min_equity": int(min_equity),
        "max_equity": int(max_equity),
    }