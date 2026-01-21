# -*- coding: utf-8 -*-
"""
ファイル: aiapp/services/daytrade/risk_math.py

これは何？
- デイトレ全自動（バックテスト/本番共通）で使う
  「損失上限・R・数量計算」を一切ブレなく提供する中核ロジック。

重要な設計方針
- 1トレード最大損失（例：3,000円）を絶対に超えない
- Qty（株数）は stop_price が決まってから計算する
- backtest / 本番 / シミュレーションで同一ロジックを使う
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


class RiskMathError(ValueError):
    """リスク計算の前提が崩れた場合の例外。"""


@dataclass(frozen=True)
class RiskBudget:
    """
    リスク予算（円ベース）

    trade_loss_yen:
      1トレードで許容する最大損失（例：3,000円）

    day_loss_yen:
      1日で許容する最大損失（例：10,000円）
    """
    trade_loss_yen: int
    day_loss_yen: int


def calc_risk_budget_yen(
    base_capital_yen: int,
    trade_loss_pct: float,
    day_loss_pct: float,
) -> RiskBudget:
    if base_capital_yen <= 0:
        raise RiskMathError("base_capital_yen must be positive.")
    if not (0 < trade_loss_pct < 1):
        raise RiskMathError("trade_loss_pct must be between 0 and 1.")
    if not (0 < day_loss_pct < 1):
        raise RiskMathError("day_loss_pct must be between 0 and 1.")

    trade_loss_yen = max(int(base_capital_yen * trade_loss_pct), 1)
    day_loss_yen = max(int(base_capital_yen * day_loss_pct), 1)

    return RiskBudget(
        trade_loss_yen=trade_loss_yen,
        day_loss_yen=day_loss_yen,
    )


def calc_r(pnl_yen: int, trade_loss_yen: int) -> float:
    if trade_loss_yen <= 0:
        raise RiskMathError("trade_loss_yen must be positive.")
    return pnl_yen / float(trade_loss_yen)


def calc_qty_from_risk_long(
    entry_price: float,
    stop_price: float,
    trade_loss_yen: int,
) -> int:
    """
    ロングの数量（株数）を計算する。

    1株あたり損失 = entry_price - stop_price
    qty = floor(trade_loss_yen / 1株あたり損失)

    これにより「最大損失 = trade_loss_yen」を必ず守る。
    """
    if entry_price <= 0 or stop_price <= 0:
        raise RiskMathError("prices must be positive.")
    if stop_price >= entry_price:
        raise RiskMathError("stop_price must be lower than entry_price.")
    if trade_loss_yen <= 0:
        raise RiskMathError("trade_loss_yen must be positive.")

    per_share_loss = entry_price - stop_price
    qty = int(trade_loss_yen // per_share_loss)
    return max(qty, 0)


def safe_qty_from_risk_long(
    entry_price: float,
    stop_price: float,
    trade_loss_yen: int,
) -> Optional[int]:
    """
    例外を投げずに数量を返す。
    計算不能なら None（＝そのトレードは見送り）
    """
    try:
        return calc_qty_from_risk_long(entry_price, stop_price, trade_loss_yen)
    except RiskMathError:
        return None