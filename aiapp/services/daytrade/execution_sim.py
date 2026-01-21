# -*- coding: utf-8 -*-
"""
ファイル: aiapp/services/daytrade/execution_sim.py

これは何？
- バックテストで「約定（注文が成立する価格）」を現実寄りにシミュレーションする部品です。
- “バックテストだけ都合よく約定する”と、本番でズレて事故るので、ここで最初から不利に寄せます。

このファイルが担当すること
- 成行注文の約定価格を決める（次足の始値を使う前提）
- スリッページ（不利なズレ）を加える
  例：slippage_pct=0.0005（=0.05%）

置き場所（重要）
- プロジェクトルート（manage.py がある階層）から見て:
  aiapp/services/daytrade/execution_sim.py

注意
- ここは「価格の決定だけ」担当します。
- 戦略（いつ入る/出る）は別ファイルでやります。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


Side = Literal["buy", "sell"]


class ExecutionSimError(ValueError):
    """約定シミュレーションの前提が崩れている場合の例外。"""


@dataclass(frozen=True)
class Fill:
    """
    約定結果（fill）

    side:
      buy or sell

    price:
      約定価格（スリッページ反映済み）

    slippage_pct:
      適用したスリッページ（0.0005なら0.05%）

    note:
      デバッグ用のメモ（任意）
    """
    side: Side
    price: float
    slippage_pct: float
    note: str = ""


def apply_slippage(price: float, side: Side, slippage_pct: float) -> float:
    """
    スリッページを適用して、不利な価格に寄せる。

    buy（買い）: 価格が上がる方向（不利）
    sell（売り）: 価格が下がる方向（不利）
    """
    if price <= 0:
        raise ExecutionSimError("price must be positive.")
    if slippage_pct < 0:
        raise ExecutionSimError("slippage_pct must be >= 0.")

    if side == "buy":
        return price * (1.0 + slippage_pct)
    if side == "sell":
        return price * (1.0 - slippage_pct)

    raise ExecutionSimError(f"invalid side: {side}")


def market_fill(next_bar_open: float, side: Side, slippage_pct: float) -> Fill:
    """
    成行注文の約定をシミュレーションする。

    ルール（フェーズ3の固定仕様）
    - 約定価格は「次足の始値」を基準にする（次足始値約定）
    - さらにスリッページで不利にする
    """
    px = apply_slippage(next_bar_open, side, slippage_pct)
    return Fill(side=side, price=px, slippage_pct=slippage_pct, note="next_bar_open + slippage")