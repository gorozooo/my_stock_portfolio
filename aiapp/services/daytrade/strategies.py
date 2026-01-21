# -*- coding: utf-8 -*-
"""
ファイル: aiapp/services/daytrade/strategies.py

これは何？
- デイトレ全自動で使う「戦略（いつ入る/いつ出る）」をまとめるファイルです。
- backtest_runner.py から呼ばれ、各バーごとに
  「enter / exit / hold」のどれを返すかを決めます。

重要（循環import対策）
- 以前は backtest_runner.py から Bar などを import していたため、
  backtest_runner.py 側も strategies.py を import してしまい循環importが発生しました。
- そのため共通型は types.py に分離し、ここでは types.py から import します。

置き場所（重要）
- プロジェクトルート（manage.py がある階層）から見て:
  aiapp/services/daytrade/strategies.py

現在入っている戦略
- VWAPPullbackLongStrategy
  → 「VWAPより上で、浅い押し目からの反発」を狙う
"""

from __future__ import annotations

from typing import Any, Dict, List

from .types import Bar, BaseStrategy, StrategySignal


class VWAPPullbackLongStrategy(BaseStrategy):
    """
    VWAP押し目ロング戦略（初心者向け・全自動耐性重視）

    エントリー条件（シンプル版）:
    1) 現在価格がVWAPより上
    2) 直近でVWAP付近まで押している（prev.low が VWAP±near_vwap_pct% 以内）
    3) 現在足が陽線（反発確認）

    イグジット条件:
    - VWAPを割った（保守）
    """

    def on_bar(self, i: int, bars: List[Bar], has_position: bool, policy: Dict[str, Any]) -> StrategySignal:
        if i < 1:
            return StrategySignal(action="hold", reason="not_enough_bars")

        bar = bars[i]
        prev = bars[i - 1]

        # --- ポリシーから必要な値（デフォルト） ---
        pullback_min = 0.0
        pullback_max = 999.0
        near_vwap_pct = 0.2  # 「%」として扱う（0.2%）

        # entry.require の中から必要項目を拾う
        entry_rules = policy.get("entry", {}).get("require", [])
        for rule in entry_rules:
            if isinstance(rule, dict) and "pullback_pct_range" in rule:
                v = rule["pullback_pct_range"]
                if isinstance(v, list) and len(v) == 2:
                    pullback_min, pullback_max = float(v[0]), float(v[1])
            if isinstance(rule, dict) and "near_vwap_pct" in rule:
                near_vwap_pct = float(rule["near_vwap_pct"])

        price = float(bar.close)
        vwap = float(bar.vwap)

        # --- エントリー ---
        if not has_position:
            # 1) 価格がVWAPより上
            if price <= vwap:
                return StrategySignal(action="hold", reason="price_below_or_equal_vwap")

            # 2) 直近でVWAP付近まで押している（prev.low が VWAP±near_vwap_pct%）
            vwap_low = vwap * (1.0 - near_vwap_pct / 100.0)
            vwap_high = vwap * (1.0 + near_vwap_pct / 100.0)
            if not (vwap_low <= float(prev.low) <= vwap_high):
                return StrategySignal(action="hold", reason="no_pullback_near_vwap")

            # 押し目の深さチェック（直近高値→prev.low の下落率）
            recent_high = max(float(prev.high), float(bar.high))
            if recent_high <= 0:
                return StrategySignal(action="hold", reason="invalid_recent_high")

            pullback_pct = (recent_high - float(prev.low)) / recent_high * 100.0
            if pullback_pct < pullback_min or pullback_pct > pullback_max:
                return StrategySignal(action="hold", reason="pullback_pct_out_of_range")

            # 3) 反発確認（陽線）
            if float(bar.close) <= float(bar.open):
                return StrategySignal(action="hold", reason="no_rebound_candle")

            return StrategySignal(action="enter", reason="vwap_pullback_rebound")

        # --- イグジット ---
        if float(bar.close) < float(bar.vwap):
            return StrategySignal(action="exit", reason="close_below_vwap")

        return StrategySignal(action="hold", reason="in_position")