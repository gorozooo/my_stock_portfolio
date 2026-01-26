# -*- coding: utf-8 -*-
"""
ファイル: aiapp/services/daytrade/judge.py

これは何？
- デイトレ戦略を「本番で回してよいか」を機械的に判定する Judge。
- バックテストの期間結果（DayResultの配列）を入力として、
  policy の judge_thresholds を基準に GO / NO_GO を返す。

思想（重要）
- 人間の感覚は一切使わない
- 1項目でも基準未達なら NO_GO
- NO_GO の場合は「理由」を必ず返す（修正指針になる）
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List


@dataclass
class JudgeResult:
    decision: str               # "GO" or "NO_GO"
    reasons: List[str]          # NO_GO の理由（GOなら空）
    metrics: Dict[str, Any]     # 判定に使った実数値


def judge_backtest_results(
    day_results: List[Any],
    policy: Dict[str, Any],
) -> JudgeResult:
    """
    期間バックテスト結果を Judge して GO / NO_GO を返す。
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
            reasons=["バックテスト日数が0です（データ不足）"],
            metrics={}
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
            max_dd_yen = min(int(max_dd_yen), int(getattr(d, "max_drawdown_yen", 0) or 0))
        except Exception:
            pass
        try:
            max_consecutive_losses = max(
                int(max_consecutive_losses),
                int(getattr(d, "max_consecutive_losses", 0) or 0),
            )
        except Exception:
            pass

    # --- 指標計算 ---
    avg_r = 0.0
    if all_trades:
        try:
            avg_r = float(sum(float(getattr(t, "r", 0.0) or 0.0) for t in all_trades)) / float(len(all_trades))
        except Exception:
            avg_r = 0.0

    base_capital = float(policy.get("capital", {}).get("base_capital", 1) or 1)
    max_dd_pct = abs(float(max_dd_yen)) / float(base_capital)
    daylimit_days_pct = float(daylimit_days) / float(total_days)

    metrics = {
        "avg_r": round(float(avg_r), 4),
        "max_dd_pct": round(float(max_dd_pct), 4),
        "max_consecutive_losses": int(max_consecutive_losses),
        "daylimit_days_pct": round(float(daylimit_days_pct), 4),
        "total_days": int(total_days),
        "total_trades": int(len(all_trades)),
    }

    # --- 判定 ---
    reasons: List[str] = []

    if float(max_dd_pct) > float(max_dd_pct_limit):
        reasons.append(
            f"最大ドローダウンが基準超え：{max_dd_pct:.2%} > {max_dd_pct_limit:.2%}"
        )

    if int(max_consecutive_losses) > int(max_consecutive_losses_limit):
        reasons.append(
            f"最大連敗数が基準超え：{max_consecutive_losses} > {max_consecutive_losses_limit}"
        )

    if float(daylimit_days_pct) > float(max_daylimit_days_pct_limit):
        reasons.append(
            f"日次損失上限ヒット率が基準超え：{daylimit_days_pct:.2%} > {max_daylimit_days_pct_limit:.2%}"
        )

    if float(avg_r) < float(min_avg_r_limit):
        reasons.append(
            f"平均Rが低すぎ：{avg_r:.3f} < {min_avg_r_limit:.3f}"
        )

    decision = "GO" if not reasons else "NO_GO"

    return JudgeResult(
        decision=decision,
        reasons=reasons,
        metrics=metrics,
    )