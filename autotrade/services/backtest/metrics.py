"""
[FILE] autotrade/services/backtest/metrics.py
[PATH] <project_root>/autotrade/services/backtest/metrics.py

このファイルは何？
- バックテストの評価指標（DD, PFなど）を計算する部品です。

初心者ポイント：
- ここを分離すると、戦略エンジンがシンプルになります。
"""

def max_drawdown(equity_curve):
    peak = -1e18
    max_dd = 0.0
    for x in equity_curve:
        peak = max(peak, x)
        dd = (peak - x) / peak if peak > 0 else 0.0
        max_dd = max(max_dd, dd)
    return max_dd


def profit_factor(pnls):
    gains = sum(x for x in pnls if x > 0)
    losses = -sum(x for x in pnls if x < 0)
    if losses <= 0:
        return 999.0 if gains > 0 else 0.0
    return gains / losses