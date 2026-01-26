# -*- coding: utf-8 -*-
"""
ファイル: aiapp/services/daytrade/judge.py

これは何？
- デイトレ戦略を「本番で回してよいか」を機械的に判定する Judge。
- バックテストの期間結果（DayResultの配列）を入力として、
  active.yml の judge_thresholds（互換） or judge_thresholds_dev/prod を基準に GO / NO-GO を返す。

思想（重要）
- 人間の感覚は一切使わない
- 1項目でも基準未達なら NO-GO
- NO-GO の場合は「理由」を必ず返す（修正指針になる）
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass
class JudgeResult:
    decision: str               # "GO" or "NO_GO"
    reasons: List[str]          # NO_GO の理由（GOなら空）
    metrics: Dict[str, Any]     # 判定に使った実数値


def _select_thresholds(policy: Dict[str, Any], judge_mode: str) -> Dict[str, Any]:
    """
    judge_mode に応じてしきい値を選ぶ。

    - prod: judge_thresholds_prod → なければ judge_thresholds（互換）
    - dev : judge_thresholds_dev  → なければ judge_thresholds（互換）
    """
    mode = (judge_mode or "prod").strip().lower()
    if mode == "dev":
        return dict(policy.get("judge_thresholds_dev", {}) or policy.get("judge_thresholds", {}) or {})
    return dict(policy.get("judge_thresholds_prod", {}) or policy.get("judge_thresholds", {}) or {})


def judge_backtest_results(
    day_results: List[Any],
    policy: Dict[str, Any],
    judge_mode: str = "prod",
) -> JudgeResult:
    """
    期間バックテスト結果を Judge して GO / NO-GO を返す。

    Parameters
    ----------
    day_results : List[DayResult]
        run_backtest_one_day の戻り値を日付分まとめたもの
    policy : dict
        load_policy_yaml().policy
    judge_mode : str
        "dev" or "prod"（デフォは prod）
    """
    thresholds = _select_thresholds(policy, judge_mode)

    max_dd_pct_limit = float(thresholds.get("max_dd_pct", 0.0))
    max_consecutive_losses_limit = int(thresholds.get("max_consecutive_losses", 0))
    max_daylimit_days_pct_limit = float(thresholds.get("max_daylimit_days_pct", 1.0))
    min_avg_r_limit = float(thresholds.get("min_avg_r", -999))

    total_days = len(day_results)
    if total_days == 0:
        return JudgeResult(
            decision="NO_GO",
            reasons=["no_backtest_days"],
            metrics={"judge_mode": judge_mode},
        )

    # --- 集計 ---
    all_trades = []
    daylimit_days = 0
    max_dd_yen = 0
    max_consecutive_losses = 0

    for d in day_results:
        all_trades.extend(getattr(d, "trades", []) or [])
        if bool(getattr(d, "day_limit_hit", False)):
            daylimit_days += 1
        try:
            max_dd_yen = min(max_dd_yen, int(getattr(d, "max_drawdown_yen", 0) or 0))
        except Exception:
            pass
        try:
            max_consecutive_losses = max(max_consecutive_losses, int(getattr(d, "max_consecutive_losses", 0) or 0))
        except Exception:
            pass

    # --- 指標計算 ---
    avg_r = 0.0
    if all_trades:
        try:
            avg_r = float(sum(getattr(t, "r", 0.0) for t in all_trades)) / float(len(all_trades))
        except Exception:
            avg_r = 0.0

    base_capital = float(policy.get("capital", {}).get("base_capital", 1) or 1)
    max_dd_pct = abs(float(max_dd_yen)) / float(base_capital)
    daylimit_days_pct = float(daylimit_days) / float(total_days)

    metrics = {
        "judge_mode": judge_mode,
        "avg_r": round(avg_r, 4),
        "max_dd_pct": round(max_dd_pct, 4),
        "max_consecutive_losses": max_consecutive_losses,
        "daylimit_days_pct": round(daylimit_days_pct, 4),
        "total_days": total_days,
        "total_trades": len(all_trades),
        "base_capital": int(base_capital),
    }

    # --- 判定 ---
    reasons: List[str] = []

    if max_dd_pct > max_dd_pct_limit:
        reasons.append(f"max_dd_pct exceeded: {max_dd_pct:.2%} > {max_dd_pct_limit:.2%}")

    if max_consecutive_losses > max_consecutive_losses_limit:
        reasons.append(f"max_consecutive_losses exceeded: {max_consecutive_losses} > {max_consecutive_losses_limit}")

    if daylimit_days_pct > max_daylimit_days_pct_limit:
        reasons.append(f"daylimit_days_pct exceeded: {daylimit_days_pct:.2%} > {max_daylimit_days_pct_limit:.2%}")

    if avg_r < min_avg_r_limit:
        reasons.append(f"avg_r too low: {avg_r:.3f} < {min_avg_r_limit:.3f}")

    decision = "GO" if not reasons else "NO_GO"

    return JudgeResult(
        decision=decision,
        reasons=reasons,
        metrics=metrics,
    )