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


def _max_drawdown_pct_and_yen(equity_curve: List[int]) -> Tuple[float, int]:
    """
    equity_curve から最大DDを計算して返す。
    - pct: 最大ドローダウン率（0.0〜）
    - yen: 最大ドローダウン額（円、>=0）
    """
    if not equity_curve:
        return 1.0, 0

    peak = int(equity_curve[0])
    max_dd_pct = 0.0
    max_dd_yen = 0

    for v in equity_curve:
        v = int(v)
        if v > peak:
            peak = v

        dd_yen = max(0, peak - v)
        dd_pct = (dd_yen / peak) if peak > 0 else 0.0

        if dd_pct > max_dd_pct:
            max_dd_pct = dd_pct
            max_dd_yen = dd_yen

    return float(max_dd_pct), int(max_dd_yen)


def _max_losing_streak_stats(
    *,
    pnls: List[int],
    equity_curve: List[int],
    base_equity: int,
) -> Dict[str, Any]:
    """
    連敗（pnl<0 が連続）に関する統計を返す。

    - max_consecutive_losses: 最大連敗数
    - max_losing_streak_pnl_yen: 連敗区間の損益（最悪の区間、負の値）
    - max_losing_streak_drawdown_yen: 連敗区間での最大沈み（円、>=0）
    - max_losing_streak_drawdown_pct: 上記の割合（base_equity基準）
    """
    if not pnls:
        return {
            "max_consecutive_losses": 0,
            "max_losing_streak_pnl_yen": 0,
            "max_losing_streak_drawdown_yen": 0,
            "max_losing_streak_drawdown_pct": 0.0,
        }

    max_streak = 0

    # 「最悪の連敗区間」を決めるための保持（損益がより悪いもの優先）
    worst_streak_pnl = 0  # もっともマイナスが大きい（例: -5000）
    worst_streak_dd_yen = 0

    cur_streak = 0
    cur_streak_start_equity = int(equity_curve[0]) if equity_curve else int(base_equity)
    cur_min_equity = cur_streak_start_equity
    cur_pnl_sum = 0

    # equity_curve は base_equity から開始して、各約定後の equity を追加してる想定
    # pnls[i] は equity_curve[i] -> equity_curve[i+1] の増分
    for i, pnl in enumerate(pnls):
        pnl = int(pnl)
        after_equity = int(equity_curve[i + 1]) if (i + 1) < len(equity_curve) else int(base_equity)

        if pnl < 0:
            # 連敗開始
            if cur_streak == 0:
                cur_streak_start_equity = int(equity_curve[i]) if i < len(equity_curve) else int(base_equity)
                cur_min_equity = cur_streak_start_equity
                cur_pnl_sum = 0

            cur_streak += 1
            cur_pnl_sum += pnl
            if after_equity < cur_min_equity:
                cur_min_equity = after_equity

            if cur_streak > max_streak:
                max_streak = cur_streak
        else:
            # 連敗終了 → 記録更新
            if cur_streak > 0:
                dd_yen = max(0, cur_streak_start_equity - cur_min_equity)

                # 「最悪」は損益がより悪い（より小さい）ものを優先
                # 同じ損益ならDDが大きい方を優先
                if (cur_pnl_sum < worst_streak_pnl) or (
                    cur_pnl_sum == worst_streak_pnl and dd_yen > worst_streak_dd_yen
                ):
                    worst_streak_pnl = int(cur_pnl_sum)
                    worst_streak_dd_yen = int(dd_yen)

            # リセット
            cur_streak = 0
            cur_pnl_sum = 0
            cur_min_equity = after_equity

    # 最後が連敗で終わった場合
    if cur_streak > 0:
        dd_yen = max(0, cur_streak_start_equity - cur_min_equity)
        if (cur_pnl_sum < worst_streak_pnl) or (
            cur_pnl_sum == worst_streak_pnl and dd_yen > worst_streak_dd_yen
        ):
            worst_streak_pnl = int(cur_pnl_sum)
            worst_streak_dd_yen = int(dd_yen)

    base = int(base_equity) if int(base_equity) > 0 else 1
    dd_pct = float(worst_streak_dd_yen) / base

    return {
        "max_consecutive_losses": int(max_streak),
        "max_losing_streak_pnl_yen": int(worst_streak_pnl),  # 負の値（最悪）
        "max_losing_streak_drawdown_yen": int(worst_streak_dd_yen),
        "max_losing_streak_drawdown_pct": round(float(dd_pct), 4),
    }


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

      "max_consecutive_losses": 5,
      "max_losing_streak_pnl_yen": -22400,
      "max_losing_streak_drawdown_yen": 31000,
      "max_losing_streak_drawdown_pct": 0.031,
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

            # 連敗系（AC/AE）
            "max_consecutive_losses": 0,
            "max_losing_streak_pnl_yen": 0,
            "max_losing_streak_drawdown_yen": 0,
            "max_losing_streak_drawdown_pct": 0.0,
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

    # DDは equity_curve から“実額”で算出（概算をやめる）
    max_dd_pct, max_dd_yen = _max_drawdown_pct_and_yen(equity_curve)

    min_equity = int(min(equity_curve)) if equity_curve else int(base_equity)
    max_equity = int(max(equity_curve)) if equity_curve else int(base_equity)

    streak_stats = _max_losing_streak_stats(
        pnls=pnls,
        equity_curve=equity_curve,
        base_equity=int(base_equity),
    )

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

        # 連敗系（AC/AE）
        **streak_stats,
    }