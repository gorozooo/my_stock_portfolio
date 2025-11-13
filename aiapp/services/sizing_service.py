# aiapp/services/sizing_service.py
# -*- coding: utf-8 -*-
"""
AI Picks 数量・必要資金・損益を証券会社別に算出するサービス（短期×攻め・本気ロジック）

- 楽天、松井の2段出力（qty, required_cash, est_pl, est_loss）
- UserSetting.risk_pct を使用
- Entry / TP / SL / ATR を使って「リスクリワード」と「手数料・スプレッド」を評価
- 条件を満たさないトレードは「0株（見送り）」として扱う
"""

from __future__ import annotations
from typing import Dict, Any

from django.db.models import Sum

from portfolio.models import BrokerAccount, CashLedger, Holding, UserSetting


# ===== パラメータ（将来はポリシー/YAML化予定の暫定値） =========================

# 手数料率（片道） 0.03% ≒ 0.0003
COMMISSION_RATE = 0.0003
# 最低手数料（片道、円）
MIN_COMMISSION_YEN = 50.0
# スプレッド＋約定ズレ想定率（往復ぶんまとめて） 0.1% ≒ 0.001
SPREAD_RATE = 0.001

# リスクリワードしきい値（R >= MIN_R で採用）
MIN_R = 1.5
# 「コストの何倍以上の利益」が必要か
MIN_PROFIT_TO_COST_MULT = 3.0
# 純利益（コスト控除後）がこれ未満なら見送り
MIN_NET_PROFIT_YEN = 2000.0


# ===== ヘルパー ===========================================================

def _get_assets(user, broker_name: str) -> float:
    """
    現金 + 株式評価額（現物/信用の区別なし）
    ※ 単ユーザー前提で BrokerAccount / Holding を user で絞る
    """
    accounts = BrokerAccount.objects.filter(user=user, broker__iexact=broker_name)

    total_cash = 0.0
    for acc in accounts:
        ledger_sum = (
            CashLedger.objects.filter(account=acc).aggregate(Sum("amount"))["amount__sum"] or 0
        )
        total_cash += float(acc.opening_balance or 0) + float(ledger_sum)

    # 保有株（現物＋信用）評価額
    holds = Holding.objects.filter(user=user, broker__iexact=broker_name)
    stock_val = 0.0
    for h in holds:
        price = float(h.last_price or 0)
        qty = float(h.quantity or 0)
        stock_val += price * qty

    return total_cash + stock_val


def _lot_size_for(code: str) -> int:
    """
    ETF/ETN (13xx / 15xx) → 1株
    日本株 → 100株
    """
    if code.startswith("13") or code.startswith("15"):
        return 1
    return 100


def _risk_pct(user) -> float:
    """
    UserSetting.risk_pct を取得
    """
    try:
        s = UserSetting.objects.get(user=user)
        return float(s.risk_pct or 1.0)
    except UserSetting.DoesNotExist:
        return 1.0


# ===== メインロジック =====================================================

def compute_position_sizing(
    user,
    code: str,
    last_price: float,
    atr: float,
    entry: float,
    tp: float,
    sl: float,
) -> Dict[str, Any]:
    """
    AI Picks 1銘柄分の数量を楽天・松井の2段で返す（短期×攻め・本気ロジック）

    返す内容：
        qty_rakuten, qty_matsui
        required_cash_rakuten, required_cash_matsui
        est_pl_rakuten, est_pl_matsui      # 手数料・スリッページ控除後の想定利益
        est_loss_rakuten, est_loss_matsui  # 損切幅にコストを含めた「最大損失イメージ」
        risk_pct, lot_size
    """
    lot = _lot_size_for(code)
    risk_pct = _risk_pct(user)

    # 価格系が壊れている場合は即座に0扱い
    if (
        not last_price or last_price <= 0 or
        not atr or atr <= 0 or
        not entry or entry <= 0 or
        not tp or tp <= 0
    ):
        return dict(
            qty_rakuten=0, qty_matsui=0,
            required_cash_rakuten=0, required_cash_matsui=0,
            est_pl_rakuten=0, est_pl_matsui=0,
            est_loss_rakuten=0, est_loss_matsui=0,
            risk_pct=risk_pct, lot_size=lot,
        )

    # ロング前提：SL は Entry より下にある想定
    # （万一 sl >= entry なら ATR ベースでフォロー）
    if sl is not None and sl < entry:
        risk_per_share = entry - sl
    else:
        # フォールバック：ATR の 0.6倍を損切幅とみなす
        risk_per_share = atr * 0.6

    reward_per_share = max(0.0, tp - entry)

    # 損切幅がゼロ/マイナスならトレード不可
    if risk_per_share <= 0:
        return dict(
            qty_rakuten=0, qty_matsui=0,
            required_cash_rakuten=0, required_cash_matsui=0,
            est_pl_rakuten=0, est_pl_matsui=0,
            est_loss_rakuten=0, est_loss_matsui=0,
            risk_pct=risk_pct, lot_size=lot,
        )

    # 証券会社別の総資産
    rakuten_assets = _get_assets(user, "楽天")
    matsui_assets = _get_assets(user, "松井")

    out: Dict[str, Any] = {}

    for broker_label, assets in [
        ("rakuten", rakuten_assets),
        ("matsui", matsui_assets),
    ]:
        if assets <= 0:
            qty = required_cash = est_pl = est_loss = 0.0
        else:
            # 1トレードあたりの許容損失
            risk_value = assets * (risk_pct / 100.0)

            # 理論株数（リスクベース）
            raw_qty = risk_value / risk_per_share if risk_per_share > 0 else 0.0
            qty = int((raw_qty // lot) * lot) if raw_qty > 0 else 0

            if qty <= 0:
                required_cash = est_pl = est_loss = 0.0
            else:
                # 必要資金（エントリー価格ベース）
                trade_value = qty * entry

                # 手数料（往復）
                one_way_commission = max(trade_value * COMMISSION_RATE, MIN_COMMISSION_YEN)
                round_trip_commission = one_way_commission * 2.0

                # スプレッド＋約定ズレ（往復まとめて）
                slippage = trade_value * SPREAD_RATE

                total_cost = round_trip_commission + slippage

                # 想定利益（TP到達時）
                gross_profit = reward_per_share * qty
                net_profit_est = gross_profit - total_cost

                # 最大損失イメージ（損切 + コスト）
                max_loss = risk_per_share * qty + total_cost

                # リスクリワード
                R = (reward_per_share / risk_per_share) if risk_per_share > 0 else 0.0

                # フィルター条件
                if (
                    R < MIN_R or                       # Rが低すぎる
                    net_profit_est < MIN_NET_PROFIT_YEN or  # 絶対額としてショボい
                    net_profit_est < total_cost * MIN_PROFIT_TO_COST_MULT  # コスト負け
                ):
                    qty = 0
                    required_cash = est_pl = est_loss = 0.0
                else:
                    required_cash = trade_value
                    est_pl = net_profit_est
                    est_loss = max_loss

        out[f"qty_{broker_label}"] = int(qty)
        out[f"required_cash_{broker_label}"] = round(required_cash, 0)
        out[f"est_pl_{broker_label}"] = round(est_pl, 0)
        out[f"est_loss_{broker_label}"] = round(est_loss, 0)

    out["risk_pct"] = risk_pct
    out["lot_size"] = lot
    return out