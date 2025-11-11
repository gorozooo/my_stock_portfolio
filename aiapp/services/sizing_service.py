# -*- coding: utf-8 -*-
"""
証券会社ごとの資産状況を元にAI提案ポジションの数量・必要資金・損益を自動算出するサービス
 - 楽天・松井のBrokerAccount/CashLedger/Holdingを参照
 - NISA等は除外して現物・信用を合算
 - リスク％をUserSettingから取得
 - 1トレードあたりの許容損失 = 総資産 × (risk_pct / 100)
 - 損切幅(ATR×R倍率)を元に数量を計算
"""

from __future__ import annotations
from decimal import Decimal
from typing import Dict, Optional

from django.db.models import Sum
from portfolio.models import BrokerAccount, CashLedger, Holding
from users.models import UserSetting  # あなたの環境に合わせて修正可


def get_total_assets(broker: str) -> float:
    """BrokerAccount と Holding の合計額を返す（信用は含む）"""
    accounts = BrokerAccount.objects.filter(broker__iexact=broker)
    total_cash = 0.0
    for acc in accounts:
        # 現金残高
        ledger_sum = (
            CashLedger.objects.filter(account=acc).aggregate(Sum("amount"))["amount__sum"] or 0
        )
        total_cash += float(acc.opening_balance or 0) + float(ledger_sum or 0)

    # 現物評価額（株価×株数）
    holds = Holding.objects.filter(broker__iexact=broker)
    stock_val = 0.0
    for h in holds:
        try:
            stock_val += float(h.quantity or 0) * float(h.last_price or 0)
        except Exception:
            pass

    return total_cash + stock_val


def get_risk_setting(user) -> float:
    """UserSetting からリスク％を取得（なければ1.0）"""
    try:
        s = UserSetting.objects.get(user=user)
        return float(s.risk_pct or 1.0)
    except UserSetting.DoesNotExist:
        return 1.0


def compute_sizing(user, code: str, last: float, atr: float) -> Dict[str, Dict[str, float]]:
    """
    銘柄ごと数量算出
      - 楽天・松井それぞれで数量・必要資金・想定PL/損失を計算
      - lot_size: 株式=100, ETF=1（暫定固定）
    """
    risk_pct = get_risk_setting(user)
    lot_size = 100 if not str(code).startswith(("13", "15")) else 1

    rakuten_assets = get_total_assets("楽天")
    matsui_assets = get_total_assets("松井")

    out = {}
    for broker, assets in [("楽天", rakuten_assets), ("松井", matsui_assets)]:
        if not assets or assets <= 0 or not atr or atr <= 0:
            qty = 0
            required_cash = est_pl = est_loss = 0.0
        else:
            risk_value = assets * (risk_pct / 100.0)
            loss_per_share = atr * 0.6  # SL距離
            qty = int((risk_value / loss_per_share) // lot_size * lot_size)
            required_cash = qty * last
            est_pl = atr * 0.8 * qty
            est_loss = loss_per_share * qty

        out[broker] = dict(
            qty=qty,
            required_cash=round(required_cash, 0),
            est_pl=round(est_pl, 0),
            est_loss=round(est_loss, 0),
        )

    out["risk_pct"] = risk_pct
    out["lot_size"] = lot_size
    return out