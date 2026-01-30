"""
[FILE] autotrade/services/strategy_decider.py
[PATH] <project_root>/autotrade/services/strategy_decider.py

このファイルは何？
- 9:30時点で、今日の戦略（BREAKOUT or VWAP）を決めるサービスです。

現状（第1弾）：
- まず動作を固めるため BREAKOUT を返します。

次フェーズ：
- “朝30分の値動き（レンジ幅/ATR的な指標）” を数値化して自動切替にします。
"""

from autotrade.models import AutoTradeDailyState


def decide_strategy(state: AutoTradeDailyState) -> str:
    # TODO: 次フェーズで本物の数値判定へ
    return "BREAKOUT"