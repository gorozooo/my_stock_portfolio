"""
[FILE] autotrade/services/decision/strategy.py
[PATH] <project_root>/autotrade/services/decision/strategy.py

このファイルは何？
- 9:30 に「今日の戦略」を決めるロジックです。
- job（時間の入口）から呼ばれる“中身”で、job本体は薄く保ちます。

初心者ポイント：
- 「難しい判定」を jobs から隔離して、あとで見返しても混乱しないようにしています。
- ここは“判断だけ”をやります（保存や画面は触りません）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Any, Optional


@dataclass(frozen=True)
class StrategyDecision:
    """
    戦略決定の出力（jobやUIでそのまま表示できる形）
    """
    strategy: str                 # "BREAKOUT" or "VWAP"
    confidence: float             # 0.0〜1.0（目安）
    reason: str                   # 初心者向けの日本語理由
    debug: Dict[str, Any]         # 内部確認用（画面に出してもOK）


def decide_strategy(
    *,
    morning_stats: Dict[str, Any],
    threshold: float = 0.012,
) -> StrategyDecision:
    """
    9:30の戦略切替（実データ判定の入口）

    引数:
      morning_stats:
        朝30分（9:00〜9:30）の統計をまとめた辞書を想定。
        例:
          {
            "range_pct": 0.015,     # 値幅（%）
            "trend_pct": 0.010,     # 始値→現在の方向（%）
            "chop_ratio": 0.60,     # 行ったり来たり度（0〜1、1ほど往復）
          }

      threshold:
        “値動きが大きい”と判断する閾値（%ではなく小数）
        例 0.012 = 1.2%

    返り値:
      StrategyDecision
    """

    # ---- 安全に取り出す（無いキーがあっても落ちない） ----
    range_pct = float(morning_stats.get("range_pct") or 0.0)
    trend_pct = float(morning_stats.get("trend_pct") or 0.0)
    chop_ratio = float(morning_stats.get("chop_ratio") or 0.0)

    # ---- 判定思想（初心者向けに超シンプル） ----
    # ・朝の値動きが大きい → ブレイク（勢いに乗る）
    # ・朝の値動きが小さい / 往復が多い → VWAP（行き過ぎを戻す）
    is_big_move = range_pct >= threshold
    is_choppy = chop_ratio >= 0.55

    if is_big_move and not is_choppy:
        strategy = "BREAKOUT"
        reason = (
            "朝の値動きがしっかりあり、往復も少なめなので、"
            "勢いに乗る『ブレイク』が向きやすいです。"
        )
        confidence = min(1.0, 0.55 + (range_pct - threshold) / max(threshold, 1e-9) * 0.25)
    else:
        strategy = "VWAP"
        reason = (
            "朝の値動きが小さめ、または往復が多めなので、"
            "行き過ぎからの戻りを狙う『VWAP押し目』が安定しやすいです。"
        )
        confidence = min(
            1.0,
            0.55
            + max(0.0, (0.60 - range_pct / max(threshold, 1e-9)) * 0.20)
            + max(0.0, (chop_ratio - 0.55) * 0.30)
        )

    debug = {
        "range_pct": range_pct,
        "trend_pct": trend_pct,
        "chop_ratio": chop_ratio,
        "threshold": threshold,
        "is_big_move": is_big_move,
        "is_choppy": is_choppy,
    }

    return StrategyDecision(
        strategy=strategy,
        confidence=float(max(0.0, min(1.0, confidence))),
        reason=reason,
        debug=debug,
    )


def decide_strategy_for_state(state) -> StrategyDecision:
    """
    互換用：既存コード（stateを渡す）から呼べる入口。

    方針：
    - 朝の統計データが取れない場合は、事故を避けるため VWAP を選びやすくする。
    - 実データの取り方は次フェーズで morning_data_service に正式接続する。
    """

    # 可能なら morning_data_service から取る（存在しなくても落ちない）
    morning_stats: Dict[str, Any] = {}
    try:
        from autotrade.services.morning_data_service import get_morning_stats  # type: ignore
        morning_stats = get_morning_stats(state=state) or {}
    except Exception:
        # まだ未実装/未接続なら空でOK（VWAP寄りになる）
        morning_stats = {}

    threshold = float(getattr(__import__("django.conf").conf.settings, "AUTOTRADE_STRATEGY_SWITCH_THRESHOLD", 0.012))
    return decide_strategy(morning_stats=morning_stats, threshold=threshold)