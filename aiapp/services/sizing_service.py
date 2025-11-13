# -*- coding: utf-8 -*-
"""
AI Picks 数量・必要資金・損益を証券会社別に算出するサービス
 - 楽天、松井の2段出力（qty, required_cash, est_pl, est_loss）
 - UserSetting.risk_pct を使用
 - ATR から損切幅を算出 → 1トレード許容損失から数量を決定
 - ETF (13xx / 15xx) は 1株、株は100株
 - user=None の場合は「全レコード＋最初のUserSetting」を使う（AI Picks 共通用）
"""

from __future__ import annotations
from typing import Dict, Any

from django.db.models import Sum

from portfolio.models import BrokerAccount, CashLedger, Holding, UserSetting


def _get_assets(user, broker_name: str) -> float:
    """
    現金 + 株式評価額（現物/信用の区別なし）

    user が None のときは、ユーザー条件を付けずに broker だけで集計する。
    （AI Picks の共通ピック用）
    """
    qs_account = BrokerAccount.objects.filter(broker__iexact=broker_name)
    if user is not None:
        qs_account = qs_account.filter(user=user)

    total_cash = 0.0
    for acc in qs_account:
        ledger_sum = (
            CashLedger.objects.filter(account=acc).aggregate(Sum("amount"))["amount__sum"] or 0
        )
        total_cash += float(acc.opening_balance or 0) + float(ledger_sum)

    # 保有株（現物＋信用）評価額
    qs_holding = Holding.objects.filter(broker__iexact=broker_name)
    if user is not None:
        qs_holding = qs_holding.filter(user=user)

    stock_val = 0.0
    for h in qs_holding:
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
    UserSetting.risk_pct を取得。
    user が None のときは一番最初の UserSetting を使う（単ユーザー運用前提）。
    どれも無ければ 1.0%
    """
    qs = UserSetting.objects.all()
    if user is not None:
        qs = qs.filter(user=user)

    s = qs.first()
    if not s:
        return 1.0
    try:
        return float(s.risk_pct or 1.0)
    except Exception:
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

    # ATR が 0 / 価格が 0 以下の場合は全部0
    if not atr or atr <= 0 or not last_price or last_price <= 0:
        return dict(
            qty_rakuten=0,
            qty_matsui=0,
            required_cash_rakuten=0,
            required_cash_matsui=0,
            est_pl_rakuten=0,
            est_pl_matsui=0,
            est_loss_rakuten=0,
            est_loss_matsui=0,
            risk_pct=risk_pct,
            lot_size=lot,
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
            qty = 0
            required_cash = 0.0
            est_pl = 0.0
            est_loss = 0.0
        else:
            # 1トレードあたりの許容損失
            risk_value = assets * (risk_pct / 100.0)

            # 損切幅：ATR の 0.6倍（あなたの旧ロジックを継承）
            loss_per_share = atr * 0.6

            # lot 単位に丸め
            qty = int((risk_value / loss_per_share) // lot * lot)
            required_cash = qty * last_price

            # 利確/損切の概算（旧ロジック継承：TP=+0.8ATR, SL=-0.6ATR）
            est_pl = atr * 0.8 * qty
            est_loss = loss_per_share * qty

        out[f"qty_{broker_label}"] = int(qty)
        out[f"required_cash_{broker_label}"] = round(required_cash, 0)
        out[f"est_pl_{broker_label}"] = round(est_pl, 0)
        out[f"est_loss_{broker_label}"] = round(est_loss, 0)

    out["risk_pct"] = risk_pct
    out["lot_size"] = lot
    return out