"""
[FILE] autotrade/services/decision/strategy.py
[PATH] <project_root>/autotrade/services/decision/strategy.py

このファイルは何？
- 9:30 に「今日の戦略」を決めるロジック（判断だけ）です。

今回の変更：
- BREAKOUT一本運用に合わせて、出力 strategy を常に "BREAKOUT" に固定
- 互換で morning_stats が来ても、debug には残す（観測は残す / 判断は固定）
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Any


@dataclass(frozen=True)
class StrategyDecision:
    """
    戦略決定の出力（jobやUIでそのまま表示できる形）
    """
    strategy: str                 # "BREAKOUT"
    confidence: float             # 0.0〜1.0（目安）
    reason: str                   # 初心者向けの日本語理由
    debug: Dict[str, Any]         # 内部確認用（画面に出してもOK）


def decide_strategy(
    *,
    morning_stats: Dict[str, Any],
    threshold: float = 0.012,
    chop_low: float = 0.50,
    chop_high: float = 0.65,
    trend_min_abs: float = 0.004,
) -> StrategyDecision:
    """
    BREAKOUT一本運用：戦略は常に "BREAKOUT"。
    morning_stats は観測ログとして debug に残す。
    """
    strategy = "BREAKOUT"
    reason = "現在は BREAKOUT 一本運用です（戦略切替は停止中）。"
    confidence = 0.70

    debug = {
        "mode": "BREAKOUT_ONLY",
        "morning_stats_raw": morning_stats,
        "threshold": float(threshold),
        "chop_low": float(chop_low),
        "chop_high": float(chop_high),
        "trend_min_abs": float(trend_min_abs),
    }

    return StrategyDecision(
        strategy=strategy,
        confidence=float(max(0.0, min(1.0, confidence))),
        reason=reason,
        debug=debug,
    )


def decide_strategy_for_state(state) -> StrategyDecision:
    """
    state（AutoTradeDailyState）から朝統計を拾い、戦略を返す入口。

    BREAKOUT一本運用：
    - state.morning_stats は debug に残す
    - 戦略は固定
    """
    morning_stats: Dict[str, Any] = {}
    try:
        ms = getattr(state, "morning_stats", None)
        if isinstance(ms, dict) and ms:
            morning_stats = ms
        else:
            morning_stats = {}
    except Exception:
        morning_stats = {}

    return decide_strategy(morning_stats=morning_stats)