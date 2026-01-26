# -*- coding: utf-8 -*-
"""
ファイル: aiapp/services/daytrade/judge.py

これは何？
- デイトレ戦略を「本番で回してよいか」を機械的に判定する Judge。
- バックテストの期間結果（DayResultの配列）を入力として、
  policy 内の judge_thresholds_* を基準に GO / NO-GO を返す。

モード
- mode="dev": judge_thresholds_dev を使う（開発用）
- mode="prod": judge_thresholds_prod を使う（本番用）
- 互換: 旧 judge_thresholds は prod 扱い
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass
class JudgeResult:
    decision: str               # "GO" or "NO_GO"
    reasons: List[str]          # NO_GO の理由（GOなら空）
    metrics: Dict[str, Any]     # 判定に使った実数値
    mode: str = "prod"          # "dev" or "prod"


def _pick_thresholds(policy: Dict[str, Any], mode: str) -> Dict[str, Any]:
    mode = (mode or "prod").strip().lower()
    if mode == "dev":
        th = policy.get("judge_thresholds_dev", {}) or {}
        if isinstance(th, dict) and th:
            return th
        # devが無い場合は prod を使う（安全側）
        mode = "prod"

    # prod
    th = policy.get("judge_thresholds_prod", None)
    if isinstance(th, dict) and th:
        return th

    # 互換: 旧キー
    th2 = policy.get("judge_thresholds", {}) or {}
    return th2 if isinstance(th2, dict) else {}


def judge_backtest_results(
    day_results: List[Any],
    policy: Dict[str, Any],
    mode: str = "prod",
) -> JudgeResult:
    thresholds = _pick_thresholds(policy, mode)
    mode = (mode or "prod").strip().lower()

    max_dd_pct_limit = float(thresholds.get("max_dd_pct", 0.0) or 0.0)
    max_consecutive_losses_limit = int(thresholds.get("max_consecutive_losses", 0) or 0)
    max_daylimit_days_pct_limit = float(thresholds.get("max_daylimit_days_pct", 1.0) or 1.0)
    min_avg_r_limit = float(thresholds.get("min_avg_r", -999) or -999)

    total_days = len(day_results or [])
    if total_days == 0:
        return JudgeResult(
            decision="NO_GO",
            reasons=["no_backtest_days"],
            metrics={},
            mode=mode,
        )

    # --- 集計 ---
    all_trades = []
    daylimit_days = 0
    max_dd_yen = 0
    max_consecutive_losses = 0

    for d in day_results:
        trades = list(getattr(d, "trades", []) or [])
        all_trades.extend(trades)

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
            avg_r = float(sum(float(getattr(t, "r", 0.0) or 0.0) for t in all_trades)) / float(len(all_trades))
        except Exception:
            avg_r = 0.0

    base_capital = float(policy.get("capital", {}).get("base_capital", 1) or 1)
    max_dd_pct = abs(float(max_dd_yen)) / float(base_capital) if base_capital > 0 else 9.0
    daylimit_days_pct = float(daylimit_days) / float(total_days) if total_days > 0 else 1.0

    metrics = {
        "avg_r": round(avg_r, 4),
        "max_dd_pct": round(max_dd_pct, 4),
        "max_consecutive_losses": int(max_consecutive_losses),
        "daylimit_days_pct": round(daylimit_days_pct, 4),
        "total_days": int(total_days),
        "total_trades": int(len(all_trades)),
        "thresholds": {
            "max_dd_pct": max_dd_pct_limit,
            "max_consecutive_losses": max_consecutive_losses_limit,
            "max_daylimit_days_pct": max_daylimit_days_pct_limit,
            "min_avg_r": min_avg_r_limit,
        },
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
        mode=mode,
    )