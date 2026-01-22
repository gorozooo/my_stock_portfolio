# -*- coding: utf-8 -*-
"""
ファイル: aiapp/services/daytrade/judge.py

これは何？
- デイトレ戦略を「本番で回してよいか」を機械的に判定する Judge。
- バックテストの期間結果（DayResultの配列）を入力として、
  active.yml の judge_thresholds を基準に GO / NO-GO を返す。

思想（重要）
- 人間の感覚は一切使わない
- 1項目でも基準未達なら NO-GO
- NO-GO の場合は「理由」を必ず返す（修正指針になる）

置き場所:
- aiapp/services/daytrade/judge.py
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List


@dataclass
class JudgeResult:
    decision: str               # "GO" or "NO_GO"
    reasons: List[str]           # NO_GO の理由（GOなら空）
    metrics: Dict[str, Any]      # 判定に使った実数値


def judge_backtest_results(
    day_results: List[Any],
    policy: Dict[str, Any],
) -> JudgeResult:
    """
    期間バックテスト結果を Judge して GO / NO-GO を返す。

    Parameters
    ----------
    day_results : List[DayResult]
        run_backtest_one_day の戻り値を日付分まとめたもの
    policy : dict
        load_policy_yaml().policy

    Returns
    -------
    JudgeResult
    """
    thresholds = policy.get("judge_thresholds", {})

    max_dd_pct_limit = float(thresholds.get("max_dd_pct", 0.0))
    max_consecutive_losses_limit = int(thresholds.get("max_consecutive_losses", 0))
    max_daylimit_days_pct_limit = float(thresholds.get("max_daylimit_days_pct", 1.0))
    min_avg_r_limit = float(thresholds.get("min_avg_r", -999))

    total_days = len(day_results)
    if total_days == 0:
        return JudgeResult(
            decision="NO_GO",
            reasons=["no_backtest_days"],
            metrics={}
        )

    # --- 集計 ---
    all_trades = []
    daylimit_days = 0
    max_dd_yen = 0
    max_consecutive_losses = 0

    for d in day_results:
        all_trades.extend(d.trades)
        if d.day_limit_hit:
            daylimit_days += 1
        max_dd_yen = min(max_dd_yen, d.max_drawdown_yen)
        max_consecutive_losses = max(max_consecutive_losses, d.max_consecutive_losses)

    # --- 指標計算 ---
    avg_r = 0.0
    if all_trades:
        avg_r = sum(t.r for t in all_trades) / len(all_trades)

    base_capital = float(policy.get("capital", {}).get("base_capital", 1))
    max_dd_pct = abs(max_dd_yen) / base_capital
    daylimit_days_pct = daylimit_days / total_days

    metrics = {
        "avg_r": round(avg_r, 4),
        "max_dd_pct": round(max_dd_pct, 4),
        "max_consecutive_losses": max_consecutive_losses,
        "daylimit_days_pct": round(daylimit_days_pct, 4),
        "total_days": total_days,
        "total_trades": len(all_trades),
    }

    # --- 判定 ---
    reasons: List[str] = []

    if max_dd_pct > max_dd_pct_limit:
        reasons.append(
            f"max_dd_pct exceeded: {max_dd_pct:.2%} > {max_dd_pct_limit:.2%}"
        )

    if max_consecutive_losses > max_consecutive_losses_limit:
        reasons.append(
            f"max_consecutive_losses exceeded: {max_consecutive_losses} > {max_consecutive_losses_limit}"
        )

    if daylimit_days_pct > max_daylimit_days_pct_limit:
        reasons.append(
            f"daylimit_days_pct exceeded: {daylimit_days_pct:.2%} > {max_daylimit_days_pct_limit:.2%}"
        )

    if avg_r < min_avg_r_limit:
        reasons.append(
            f"avg_r too low: {avg_r:.3f} < {min_avg_r_limit:.3f}"
        )

    decision = "GO" if not reasons else "NO_GO"

    return JudgeResult(
        decision=decision,
        reasons=reasons,
        metrics=metrics,
    )