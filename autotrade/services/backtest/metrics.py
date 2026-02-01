"""
[FILE] autotrade/services/backtest/metrics.py
[PATH] <project_root>/autotrade/services/backtest/metrics.py

このファイルは何？
- バックテスト結果（トレード履歴）から、
  人間が判断できる「成績指標」を計算する部品です。

このファイルの役割：
- engine は「取引の結果」を出すだけ
- metrics は「それを評価」するだけ

初心者ポイント：
- 数字の意味が分からなくなったら、
  まずここを見ると「何を計算しているか」が分かります。
"""

from typing import List, Dict


# =========================================================
# 既存：最大ドローダウン
# =========================================================
def max_drawdown(equity_curve: List[float]) -> float:
    """
    最大ドローダウン（資産が一番へこんだ割合）

    例：
    100万 → 90万 → 95万
    → 最大ドローダウン = 10%
    """
    peak = -1e18
    max_dd = 0.0

    for x in equity_curve:
        peak = max(peak, x)
        if peak > 0:
            dd = (peak - x) / peak
        else:
            dd = 0.0
        max_dd = max(max_dd, dd)

    return max_dd


# =========================================================
# 既存：プロフィットファクター
# =========================================================
def profit_factor(pnls: List[float]) -> float:
    """
    プロフィットファクター（利益 ÷ 損失）

    1.0 未満：負け
    1.0 超え：勝ち
    """
    gains = sum(x for x in pnls if x > 0)
    losses = -sum(x for x in pnls if x < 0)

    if losses <= 0:
        return 999.0 if gains > 0 else 0.0

    return gains / losses


# =========================================================
# 新規：基本指標（初心者が一番見るやつ）
# =========================================================
def total_pnl(pnls: List[float]) -> float:
    """
    合計損益（円）

    + なら勝ち、- なら負け
    """
    return sum(pnls)


def win_rate(pnls: List[float]) -> float:
    """
    勝率（0.0〜1.0）

    例：
    10回中 6勝 → 0.6（=60%）
    """
    if not pnls:
        return 0.0

    wins = sum(1 for x in pnls if x > 0)
    return wins / len(pnls)


def expectancy(pnls: List[float]) -> float:
    """
    1回あたりの期待値（円）

    プラスなら、長期的に増える可能性が高い
    マイナスなら、回すほど減る
    """
    if not pnls:
        return 0.0
    return sum(pnls) / len(pnls)


# =========================================================
# 集約：UI / DB 保存用のまとめ関数
# =========================================================
def summarize_metrics(
    *,
    pnls: List[float],
    equity_curve: List[float],
) -> Dict[str, float]:
    """
    バックテスト結果を「そのまま画面に出せる形」にまとめる

    戻り値の例：
    {
      "total_pnl": 12500,
      "win_rate": 0.58,
      "expectancy": 320,
      "profit_factor": 1.42,
      "max_drawdown_pct": 0.032,
      "trades": 38,
    }
    """

    return {
        # 🔰 初心者がまず見る
        "total_pnl": total_pnl(pnls),
        "win_rate": win_rate(pnls),
        "expectancy": expectancy(pnls),

        # 📊 少し慣れたら見る
        "profit_factor": profit_factor(pnls),
        "max_drawdown_pct": max_drawdown(equity_curve),

        # 参考情報
        "trades": len(pnls),
    }