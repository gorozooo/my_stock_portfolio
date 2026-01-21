# -*- coding: utf-8 -*-
"""
ファイル: aiapp/services/daytrade/strategies.py

これは何？
- デイトレ全自動で使う「戦略（いつ入る/いつ出る）」をまとめるファイルです。
- backtest_runner.py から呼ばれ、各バーごとに
  「enter / exit / hold」のどれを返すかを決めます。

このファイルの役割
- ルール判断“だけ”を担当する
- 約定・数量・損益計算は別ファイルに任せる（責任分離）

現在入っている戦略
- VWAPPullbackLongStrategy
  → 「VWAPより上で、浅い押し目からの反発」を狙う
"""

from __future__ import annotations

from typing import Any, Dict, List

from .backtest_runner import Bar, BaseStrategy, StrategySignal


class VWAPPullbackLongStrategy(BaseStrategy):
    """
    VWAP押し目ロング戦略（初心者向け・全自動耐性重視）

    エントリー条件（シンプル版）:
    1) 現在価格がVWAPより上
    2) 直近でVWAP付近まで押している
    3) 現在足が陽線（反発確認）

    イグジット条件:
    - VWAPを明確に割った
    - 利益確定は backtest_runner 側の時間切れ/引け処理に任せる
    """

    def on_bar(self, i: int, bars: List[Bar], has_position: bool, policy: Dict[str, Any]) -> StrategySignal:
        # 最低2本ないと判断できない
        if i < 1:
            return StrategySignal(action="hold", reason="not enough bars")

        bar = bars[i]
        prev = bars[i - 1]

        # --- ポリシーから必要な値 ---
        entry_rules = policy["entry"]["require"]

        # 押し目幅の許容レンジ（%）
        pullback_min = 0.0
        pullback_max = 1.0
        near_vwap_pct = 0.2

        for rule in entry_rules:
            if "pullback_pct_range" in rule:
                pullback_min, pullback_max = rule["pullback_pct_range"]
            if "near_vwap_pct" in rule:
                near_vwap_pct = rule["near_vwap_pct"]

        # --- 共通計算 ---
        price = bar.close
        vwap = bar.vwap

        # --- エントリー判定 ---
        if not has_position:
            # 1) 価格がVWAPより上
            if price <= vwap:
                return StrategySignal(action="hold", reason="price_below_vwap")

            # 2) 直近でVWAP付近まで押しているか
            #    （直前足の安値がVWAP±near_vwap_pct%以内）
            vwap_low = vwap * (1.0 - near_vwap_pct / 100.0)
            vwap_high = vwap * (1.0 + near_vwap_pct / 100.0)

            if not (vwap_low <= prev.low <= vwap_high):
                return StrategySignal(action="hold", reason="no_pullback_near_vwap")

            # 押し目の深さチェック（高値→安値の下落率）
            recent_high = max(prev.high, bar.high)
            pullback_pct = (recent_high - prev.low) / recent_high * 100.0
            if pullback_pct < pullback_min or pullback_pct > pullback_max:
                return StrategySignal(action="hold", reason="pullback_pct_out_of_range")

            # 3) 反発確認（陽線）
            if bar.close <= bar.open:
                return StrategySignal(action="hold", reason="no_rebound")

            return StrategySignal(action="enter", reason="vwap_pullback_rebound")

        # --- イグジット判定 ---
        else:
            # VWAP割れで撤退（保守）
            if bar.close < vwap:
                return StrategySignal(action="exit", reason="close_below_vwap")

            return StrategySignal(action="hold", reason="in_position")