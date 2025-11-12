# -*- coding: utf-8 -*-
"""
AI Picks 数量・必要資金・損益を証券会社別に算出するサービス
 - 楽天、松井の2段出力（qty, required_cash, est_pl, est_loss）
 - UserSetting.risk_pct を使用
 - ATR から損切幅を算出 → 1トレード許容損失から数量を決定
 - ETF (13xx / 15xx) は 1株、株は100株
"""

from __future__ import annotations
from typing import Dict, Any
from django.db.models import Sum

from portfolio.models import BrokerAccount, CashLedger, Holding, UserSetting


def _get_assets(user, broker_name: str) -> float:
    """
    現金 + 株式評価額（現物/信用の区別なし）
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


def compute_position_sizing(
    user,
    code: str,
    last_price: float,
    atr: float,
) -> Dict[str, Any]:
    """
    AI Picks 1銘柄分の数量を楽天・松井の2段で返す
    返す内容：
        qty_rakuten, qty_matsui
        required_cash_rakuten, required_cash_matsui
        est_pl_rakuten, est_pl_matsui
        est_loss_rakuten, est_loss_matsui
        risk_pct, lot_size
    """
    lot = _lot_size_for(code)
    risk_pct = _risk_pct(user)

    # ATR が 0 の場合は全部0
    if not atr or atr <= 0 or last_price <= 0:
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

    out = {}

    for broker_label, assets in [
        ("rakuten", rakuten_assets),
        ("matsui", matsui_assets),
    ]:
        if assets <= 0:
            qty = required_cash = est_pl = est_loss = 0
        else:
            # 1トレードあたりの許容損失
            risk_value = assets * (risk_pct / 100.0)

            # 損切幅：ATR の 0.6倍（あなたの旧ロジックを継承）
            loss_per_share = atr * 0.6

            qty = int((risk_value / loss_per_share) // lot * lot)
            required_cash = qty * last_price

            # 利確/損切の概算（旧ロジック継承）
            est_pl = atr * 0.8 * qty
            est_loss = loss_per_share * qty

        out[f"qty_{broker_label}"] = qty
        out[f"required_cash_{broker_label}"] = round(required_cash, 0)
        out[f"est_pl_{broker_label}"] = round(est_pl, 0)
        out[f"est_loss_{broker_label}"] = round(est_loss, 0)

    out["risk_pct"] = risk_pct
    out["lot_size"] = lot
    return out