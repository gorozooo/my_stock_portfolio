from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Any


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
    chop_low: float = 0.50,
    chop_high: float = 0.65,
    trend_min_abs: float = 0.004,
) -> StrategyDecision:
    """
    9:30の戦略切替（実データ判定）

    引数:
      morning_stats:
        朝30分（9:00〜9:30）の統計をまとめた辞書を想定。
        例:
          {
            "range_pct": 0.015,     # 値幅（1.5%）
            "trend_pct": 0.010,     # 始値→終値の方向（+1.0% など）
            "chop_ratio": 0.60,     # 行ったり来たり度（0〜1、1ほど往復）
          }

      threshold:
        “値動きが大きい”と判断する閾値（小数）
        例 0.012 = 1.2%

      chop_low / chop_high:
        往復度(chop_ratio)の「グレーゾーン」を作るための境界
        - chop_ratio <= chop_low  : 往復が少ない（トレンド寄り）
        - chop_low < ... < chop_high : グレー（どっちとも言える）
        - chop_ratio >= chop_high : 往復が多い（VWAP寄り）

      trend_min_abs:
        グレーゾーンの時に「方向感がある」と言える最低ライン
        例 0.004 = 0.4%
    """

    # ---- 安全に取り出す（無いキーがあっても落ちない） ----
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

    # ---- 判定の土台 ----
    # ・朝の値動きが大きい → ブレイク候補
    # ・ただし往復が多いなら危ないのでVWAP寄り
    is_big_move = range_pct >= threshold

    # 往復度のゾーン分け
    is_trendy = chop_ratio <= chop_low
    is_choppy = chop_ratio >= chop_high
    is_gray = (not is_trendy) and (not is_choppy)  # ちょうど中間

    # ---- 結論 ----
    # 1) 値動きが大きい + 往復が少ない → BREAKOUT
    # 2) 値動きが大きい + グレー → 方向感(trend_pct)で決める
    # 3) それ以外（値動き小さい or 往復多い） → VWAP
    if is_big_move and is_trendy:
        strategy = "BREAKOUT"
        reason = (
            "朝の値動きがしっかりあり、行ったり来たりも少なめなので、"
            "勢いに乗る『ブレイク』が向きやすいです。"
        )

        # range_pct が threshold をどれだけ上回ったかで自信を上げる（上げすぎない）
        bump = 0.0
        if threshold > 0:
            bump = (range_pct - threshold) / threshold
        confidence = 0.62 + max(0.0, min(0.35, bump * 0.22))

    elif is_big_move and is_gray:
        # グレーの日は「方向感」があるならブレイク、なければVWAP
        has_trend = abs(trend_pct) >= trend_min_abs

        if has_trend:
            strategy = "BREAKOUT"
            reason = (
                "朝の値動きは大きく、行ったり来たりは中くらいですが、"
                "方向感も出ているため『ブレイク』で取りやすい状況です。"
            )
            # グレーなので自信は控えめスタート
            # trend が強いほど少し上げる
            t = min(1.0, abs(trend_pct) / max(trend_min_abs, 1e-9))
            confidence = 0.58 + min(0.25, t * 0.18) + min(0.10, (range_pct / max(threshold, 1e-9) - 1.0) * 0.05)
        else:
            strategy = "VWAP"
            reason = (
                "朝の値動きは大きいですが、行ったり来たりも混ざっていて"
                "方向感が弱いので、行き過ぎからの戻りを狙う『VWAP押し目』が安定しやすいです。"
            )
            # グレーVWAPはやや低め
            confidence = 0.58 + min(0.20, (chop_ratio - chop_low) / max((chop_high - chop_low), 1e-9) * 0.20)

    else:
        # 値動きが小さい or 往復が多い → VWAP
        strategy = "VWAP"
        reason = (
            "朝の値動きが小さめ、または行ったり来たりが多めなので、"
            "行き過ぎからの戻りを狙う『VWAP押し目』が安定しやすいです。"
        )
        # chopが高いほどVWAP寄りの自信が上がる
        # big_move じゃないなら控えめ
        base = 0.60 if not is_big_move else 0.58
        confidence = base + max(0.0, min(0.35, (chop_ratio - 0.55) * 0.50))

    debug = {
        "range_pct": float(range_pct),
        "trend_pct": float(trend_pct),
        "chop_ratio": float(chop_ratio),
        "threshold": float(threshold),
        "chop_low": float(chop_low),
        "chop_high": float(chop_high),
        "trend_min_abs": float(trend_min_abs),
        "is_big_move": bool(is_big_move),
        "is_trendy": bool(is_trendy),
        "is_gray": bool(is_gray),
        "is_choppy": bool(is_choppy),
        "morning_stats_raw": morning_stats,
    }

    return StrategyDecision(
        strategy=strategy,
        confidence=float(max(0.0, min(1.0, confidence))),
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

    # 1) まず「固定保存された朝統計」を優先
    morning_stats: Dict[str, Any] = {}
    try:
        ms = getattr(state, "morning_stats", None)
        if isinstance(ms, dict) and ms:
            morning_stats = ms
        else:
            morning_stats = {}
    except Exception:
        morning_stats = {}

    # 2) 無ければ計算（互換）
    if not morning_stats:
        try:
            from autotrade.services.universe.morning_data_service import get_morning_stats
            morning_stats = get_morning_stats(state=state) or {}
        except Exception:
            morning_stats = {}

    threshold = float(getattr(settings, "AUTOTRADE_STRATEGY_SWITCH_THRESHOLD", 0.012))

    # グレーゾーン境界（設定があれば上書きできるようにする）
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