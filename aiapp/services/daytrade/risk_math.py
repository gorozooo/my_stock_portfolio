# -*- coding: utf-8 -*-
"""
ファイル: aiapp/services/daytrade/risk_math.py

これは何？
- デイトレ全自動（バックテスト/本番共通）で使う「リスク計算」を1か所に集約したファイルです。
- “損切りは固定で守る”という前提のもと、以下を共通ロジックとして提供します。

提供する機能（このファイルでできること）
1) 1トレードの最大損失（円）を計算
   - 例：資金100万円 × 0.3% = 3,000円

2) 1日の最大損失（円）を計算（デイリミット）
   - 例：資金100万円 × 1% = 10,000円

3) R（アール）を計算
   - R = 損益(円) ÷ 1トレード最大損失(円)
   - 例：+4,500円の利益 → +1.5R（損失許容3,000円の場合）

4) 数量（株数）を計算（最重要）
   - エントリー価格と損切り価格が決まれば、
     “最大損失が3,000円以内”になる株数を自動で決めます。
   - 場中を見られない全自動では、この計算が命です。

置き場所（重要）
- プロジェクトルート（manage.py がある階層）から見て:
  aiapp/services/daytrade/risk_math.py

注意（重要）
- このファイルは「計算だけ」を担当します。
  DBアクセス、API呼び出し、銘柄選定などは一切しません。
  → だから壊れにくい。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


class RiskMathError(ValueError):
    """リスク計算の前提が崩れている場合に投げる例外。"""


@dataclass(frozen=True)
class RiskBudget:
    """
    リスク予算（円ベース）をまとめたもの。

    trade_loss_yen:
      1トレードで許容する最大損失（例：3,000円）

    day_loss_yen:
      1日で許容する最大損失（例：10,000円）
    """
    trade_loss_yen: int
    day_loss_yen: int


def calc_risk_budget_yen(base_capital_yen: int, trade_loss_pct: float, day_loss_pct: float) -> RiskBudget:
    """
    資金と%から、トレード/日次の損失上限（円）を計算する。

    例:
      base_capital_yen=1,000,000
      trade_loss_pct=0.003  → 3,000円
      day_loss_pct=0.01     → 10,000円

    仕様:
    - 円は整数に丸める（小数は切り捨て）
    """
    if base_capital_yen <= 0:
        raise RiskMathError("base_capital_yen must be positive.")
    if trade_loss_pct <= 0 or trade_loss_pct >= 1:
        raise RiskMathError("trade_loss_pct must be between (0, 1).")
    if day_loss_pct <= 0 or day_loss_pct >= 1:
        raise RiskMathError("day_loss_pct must be between (0, 1).")

    trade_loss_yen = int(base_capital_yen * trade_loss_pct)
    day_loss_yen = int(base_capital_yen * day_loss_pct)

    # 最低1円は確保（極端な設定ミスでもゼロにならないようにする）
    trade_loss_yen = max(trade_loss_yen, 1)
    day_loss_yen = max(day_loss_yen, 1)

    return RiskBudget(trade_loss_yen=trade_loss_yen, day_loss_yen=day_loss_yen)


def calc_r(pnl_yen: int, trade_loss_yen: int) -> float:
    """
    R（アール）を計算する。

    R = 損益(円) / 1トレード最大損失(円)

    例:
      trade_loss_yen=3000
      pnl_yen=+4500  → +1.5R
      pnl_yen=-3000  → -1.0R
    """
    if trade_loss_yen <= 0:
        raise RiskMathError("trade_loss_yen must be positive.")
    return pnl_yen / float(trade_loss_yen)


def calc_stop_price_for_long(entry_price: float, trade_loss_yen: int, qty: int) -> float:
    """
    ロング（買い）の場合の損切り価格を計算する（参考用）。

    stop_price = entry_price - (trade_loss_yen / qty)

    注意:
    - 実運用では、先に stop_price を決めて qty を出す方が多い。
    - これは「qtyが決まっているときに、最大損失に合わせたstopを計算」する用途。
    """
    if entry_price <= 0:
        raise RiskMathError("entry_price must be positive.")
    if trade_loss_yen <= 0:
        raise RiskMathError("trade_loss_yen must be positive.")
    if qty <= 0:
        raise RiskMathError("qty must be positive.")
    return entry_price - (trade_loss_yen / float(qty))


def calc_qty_from_risk_long(entry_price: float, stop_price: float, trade_loss_yen: int) -> int:
    """
    ロング（買い）の数量（株数）を計算する。

    目的:
    - entry_price（エントリー）と stop_price（損切り）が決まったら、
      “最大損失が trade_loss_yen を超えない”株数を返す。

    計算:
      1株あたりの損失 = entry_price - stop_price
      qty = floor(trade_loss_yen / (1株あたりの損失))

    例:
      entry=1000円, stop=990円 → 1株損失=10円
      trade_loss_yen=3000円 → qty=300株

    注意:
    - 1株あたり損失が0以下（stopがentry以上）は危険なのでエラーにする。
    """
    if entry_price <= 0:
        raise RiskMathError("entry_price must be positive.")
    if stop_price <= 0:
        raise RiskMathError("stop_price must be positive.")
    if trade_loss_yen <= 0:
        raise RiskMathError("trade_loss_yen must be positive.")

    per_share_loss = entry_price - stop_price
    if per_share_loss <= 0:
        raise RiskMathError("stop_price must be lower than entry_price for long.")

    qty = int(trade_loss_yen // per_share_loss)
    return max(qty, 0)


def calc_pnl_yen_long(entry_price: float, exit_price: float, qty: int, fee_yen: int = 0) -> int:
    """
    ロング（買い）の損益（円）を計算する（単純版）。

    pnl = (exit - entry) * qty - fee

    手数料は将来拡張できるので、まずは固定額で受け取れるようにする。
    """
    if qty <= 0:
        return 0
    if entry_price <= 0 or exit_price <= 0:
        raise RiskMathError("prices must be positive.")
    if fee_yen < 0:
        raise RiskMathError("fee_yen must be >= 0.")
    pnl = (exit_price - entry_price) * qty
    return int(pnl) - int(fee_yen)


def calc_trade_loss_yen_from_policy(policy: dict) -> int:
    """
    policy(dict) から 1トレード最大損失（円）を取り出して計算するヘルパー。
    例: policy['capital']['base_capital'] と policy['risk']['trade_loss_pct'] を使う。
    """
    base_capital = int(policy["capital"]["base_capital"])
    trade_loss_pct = float(policy["risk"]["trade_loss_pct"])
    day_loss_pct = float(policy["risk"]["day_loss_pct"])
    budget = calc_risk_budget_yen(base_capital, trade_loss_pct, day_loss_pct)
    return budget.trade_loss_yen


def calc_day_loss_yen_from_policy(policy: dict) -> int:
    """
    policy(dict) から 1日最大損失（円）を取り出して計算するヘルパー。
    """
    base_capital = int(policy["capital"]["base_capital"])
    trade_loss_pct = float(policy["risk"]["trade_loss_pct"])
    day_loss_pct = float(policy["risk"]["day_loss_pct"])
    budget = calc_risk_budget_yen(base_capital, trade_loss_pct, day_loss_pct)
    return budget.day_loss_yen


def safe_qty_for_long(entry_price: float, stop_price: float, trade_loss_yen: int) -> Optional[int]:
    """
    例外を投げずに、数量計算できるなら qty を返す。
    計算不能なら None を返す（バックテストや全自動で“見送り”に使える）。

    使いどころ:
    - stopが不正 → 見送り
    - 価格データが変 → 見送り
    """
    try:
        qty = calc_qty_from_risk_long(entry_price, stop_price, trade_loss_yen)
    except RiskMathError:
        return None
    return qty