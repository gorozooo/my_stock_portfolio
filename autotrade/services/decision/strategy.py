"""
[FILE] autotrade/services/decision/strategy.py
[PATH] <project_root>/autotrade/services/decision/strategy.py

このファイルは何？
- 9:30 に「今日の戦略」を決めるロジック（判断だけ）です。

役割（BREAKOUT一本運用）：
  1) 戦略は固定で BREAKOUT（切替ロジック撤去）
  2) confidence（目安）と初心者向け理由文を生成
  3) debug（内部確認用）を返す（保存や画面は触らない）

初心者ポイント：
- “判断だけ” をここに閉じ込めると、ジョブやUIが肥大化しません。
- 再現性のため、state.morning_stats がある場合はそれを優先します。

変更点：
- 常に BREAKOUT を返す
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
    9:30の戦略決定（BREAKOUT固定）

    引数は互換のため残す（古い呼び出しが壊れない）。
    morning_stats は confidence / reason の素材に使う。
    """

    def _safe_float(x: Any, default: float = 0.0) -> float:
        try:
            if x is None:
                return float(default)
            return float(x)
        except Exception:
            return float(default)

    range_pct = _safe_float(morning_stats.get("range_pct"), 0.0)
    trend_pct = _safe_float(morning_stats.get("trend_pct"), 0.0)
    chop_ratio = _safe_float(morning_stats.get("chop_ratio"), 0.0)

    threshold = float(threshold)
    chop_low = float(chop_low)
    chop_high = float(chop_high)
    trend_min_abs = float(trend_min_abs)

    # ---- BREAKOUT固定 ----
    strategy = "BREAKOUT"

    # confidence（目安）
    # ・朝の値動きが大きいほど上げる
    # ・方向感があるほど少し上げる
    # ・往復が多いほど少し下げる（ただし戦略は変えない）
    big_move = (range_pct >= threshold) if threshold > 0 else False
    bump = 0.0
    if threshold > 0:
        bump = max(0.0, min(1.5, (range_pct - threshold) / threshold))  # 0〜1.5程度に抑える

    t = 0.0
    if trend_min_abs > 0:
        t = min(1.5, abs(trend_pct) / max(trend_min_abs, 1e-9))  # 0〜1.5程度

    ch = 0.0
    # chop_ratio が高い＝往復多い＝難しい → 自信を少し下げる
    if chop_ratio > 0:
        ch = max(0.0, min(1.0, (chop_ratio - 0.55) / 0.25))  # 0.55→0, 0.80→1 目安

    confidence = 0.60
    confidence += min(0.25, bump * 0.12)
    confidence += min(0.18, t * 0.10)
    confidence -= min(0.18, ch * 0.15)

    confidence = float(max(0.0, min(1.0, confidence)))

    # reason（初心者向け）
    if big_move:
        reason = (
            "今日は戦略は『ブレイクアウト』に固定です。"
            "朝の値動きが大きめなので、動いた方向に乗る前提で組み立てます。"
        )
        if abs(trend_pct) >= trend_min_abs:
            reason += " さらに方向感も出ているため、ブレイクの条件に合いやすい状況です。"
        else:
            reason += " ただし方向感が弱い場合はダマシが増えるので、エントリー条件は厳しめに見ます。"
    else:
        reason = (
            "今日は戦略は『ブレイクアウト』に固定です。"
            "朝の値動きが小さめの日はブレイクが起きにくいので、条件を満たした時だけ入ります（見送り多めでOK）。"
        )

    debug = {
        "mode": "BREAKOUT_ONLY",
        "range_pct": float(range_pct),
        "trend_pct": float(trend_pct),
        "chop_ratio": float(chop_ratio),
        "threshold": float(threshold),
        "chop_low": float(chop_low),
        "chop_high": float(chop_high),
        "trend_min_abs": float(trend_min_abs),
        "big_move": bool(big_move),
        "confidence_parts": {
            "base": 0.60,
            "bump": float(min(0.25, bump * 0.12)),
            "trend": float(min(0.18, t * 0.10)),
            "chop_penalty": float(min(0.18, ch * 0.15)),
        },
        "morning_stats_raw": morning_stats,
    }

    return StrategyDecision(
        strategy=strategy,
        confidence=confidence,
        reason=reason,
        debug=debug,
    )


def decide_strategy_for_state(state) -> StrategyDecision:
    """
    state（AutoTradeDailyState）から朝統計を作って、戦略を決める入口。

    方針（再現性ファースト）：
    - state.morning_stats がある場合は、それを「正」として使う（9:30で固定）
    - 無い場合だけ morning_data_service で計算する（互換用）
    """
    from django.conf import settings

    morning_stats: Dict[str, Any] = {}
    try:
        ms = getattr(state, "morning_stats", None)
        if isinstance(ms, dict) and ms:
            morning_stats = ms
        else:
            morning_stats = {}
    except Exception:
        morning_stats = {}

    if not morning_stats:
        try:
            from autotrade.services.universe.morning_data_service import get_morning_stats
            morning_stats = get_morning_stats(state=state) or {}
        except Exception:
            morning_stats = {}

    threshold = float(getattr(settings, "AUTOTRADE_STRATEGY_SWITCH_THRESHOLD", 0.012))
    chop_low = float(getattr(settings, "AUTOTRADE_CHOP_LOW", 0.50))
    chop_high = float(getattr(settings, "AUTOTRADE_CHOP_HIGH", 0.65))
    trend_min_abs = float(getattr(settings, "AUTOTRADE_TREND_MIN_ABS", 0.004))

    return decide_strategy(
        morning_stats=morning_stats,
        threshold=threshold,
        chop_low=chop_low,
        chop_high=chop_high,
        trend_min_abs=trend_min_abs,
    )