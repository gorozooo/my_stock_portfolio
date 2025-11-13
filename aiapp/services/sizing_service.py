# -*- coding: utf-8 -*-
"""
AI Picks 数量・必要資金・損益を証券会社別に算出するサービス

- 楽天・松井の 2 段出力（qty, required_cash, est_pl, est_loss）
- UserSetting.risk_pct を使用
- ATR から損切り幅を算出 → 1トレード許容損失から数量を決定
- ETF (13xx / 15xx) は 1 株、通常株は 100 株
- ★ 証券サマリで計算している「信用余力（概算）」を上限として、
    その余力を超えるロットは出さない
"""

from __future__ import annotations
from typing import Dict, Any

from django.db.models import Sum

from portfolio.models import BrokerAccount, CashLedger, Holding, UserSetting
from aiapp.services.broker_summary import compute_broker_summaries


# =========================
# ヘルパ
# =========================

def _get_assets(user, broker_name: str) -> float:
    """
    現金 + 株式評価額（現物/信用の区別なし）をざっくり「総資産」として計算。
    リスク％から 1 トレード許容損失を出すために使う。
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
    通常の日本株 → 100株
    """
    if code.startswith("13") or code.startswith("15"):
        return 1
    return 100


def _risk_pct(user) -> float:
    """
    UserSetting.risk_pct を取得（なければ 1.0）
    """
    try:
        s = UserSetting.objects.get(user=user)
        return float(s.risk_pct or 1.0)
    except UserSetting.DoesNotExist:
        return 1.0


def _leverage_params(user) -> Dict[str, float]:
    """
    UserSetting から倍率/ヘアカットを取得。
    無い場合はデフォルト値で補完。
    """
    try:
        us = UserSetting.objects.get(user=user)
    except UserSetting.DoesNotExist:
        return {
            "rakuten_leverage": 2.90,
            "rakuten_haircut": 0.30,
            "matsui_leverage": 2.80,
            "matsui_haircut": 0.00,
        }

    def _get(obj, name, default):
        return float(getattr(obj, name, default) or default)

    return {
        "rakuten_leverage": _get(us, "leverage_rakuten", 2.90),
        "rakuten_haircut":  _get(us, "haircut_rakuten", 0.30),
        "matsui_leverage":  _get(us, "leverage_matsui", 2.80),
        "matsui_haircut":   _get(us, "haircut_matsui", 0.00),
    }


# =========================
# メイン：数量計算
# =========================

def compute_position_sizing(
    user,
    code: str,
    last_price: float,
    atr: float,
) -> Dict[str, Any]:
    """
    AI Picks 1銘柄分の数量を 楽天・松井 の 2 段で返す。

    返す内容：
        qty_rakuten, qty_matsui
        required_cash_rakuten, required_cash_matsui
        est_pl_rakuten, est_pl_matsui
        est_loss_rakuten, est_loss_matsui
        risk_pct, lot_size

    ★ それぞれの数量は、
        ・リスク％から計算された最大ロット
        ・AI設定 > 証券サマリで出している「信用余力（概算）」
      の両方を満たすように min() でクリップする。
    """
    lot = _lot_size_for(code)
    risk_pct = _risk_pct(user)

    # ATR や価格が 0/未定義なら全部 0
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

    # 証券会社別 総資産（リスク計算用）
    rakuten_assets = _get_assets(user, "楽天")
    matsui_assets = _get_assets(user, "松井")

    # 設定されている倍率/ヘアカットを取得し、
    # 証券サマリのロジックと同じ compute_broker_summaries から
    # 「信用余力（概算）」を拾う。
    lev_params = _leverage_params(user)
    broker_rows = compute_broker_summaries(
        user=user,
        risk_pct=risk_pct,
        rakuten_leverage=lev_params["rakuten_leverage"],
        rakuten_haircut=lev_params["rakuten_haircut"],
        matsui_leverage=lev_params["matsui_leverage"],
        matsui_haircut=lev_params["matsui_haircut"],
    )

    credit_yoryoku_map: Dict[str, float] = {}
    for b in broker_rows:
        label = b.get("label")
        if not label:
            continue
        credit_yoryoku_map[label] = float(b.get("credit_yoryoku") or 0.0)

    out: Dict[str, Any] = {}

    for broker_label, assets in [
        ("楽天", rakuten_assets),
        ("松井", matsui_assets),
    ]:
        # デフォルト 0
        qty = required_cash = est_pl = est_loss = 0.0

        if assets > 0:
            # 1トレードあたり許容損失（リスクベース）
            risk_value = assets * (risk_pct / 100.0)

            # 損切幅：ATR の 0.6倍（既存ロジックを継承）
            loss_per_share = atr * 0.6

            if loss_per_share > 0:
                # ① リスク％から出る最大ロット
                qty_risk = int((risk_value / loss_per_share) // lot * lot)

                # ② 信用余力（概算）から出る最大ロット
                credit_yoryoku = credit_yoryoku_map.get(broker_label, 0.0)
                # 「1単元買うのに必要な最低コスト」
                one_lot_cash = last_price * lot
                if credit_yoryoku <= 0 or one_lot_cash <= 0:
                    qty_limit = 0
                else:
                    qty_limit = int(credit_yoryoku // one_lot_cash) * lot

                # ③ 両方を満たすロットだけ採用
                qty = max(0, min(qty_risk, qty_limit))

                required_cash = qty * last_price
                # 利確/損切の概算（旧ロジック継承）
                est_pl = atr * 0.8 * qty
                est_loss = loss_per_share * qty

        key = "rakuten" if broker_label == "楽天" else "matsui"
        out[f"qty_{key}"] = int(qty)
        out[f"required_cash_{key}"] = round(required_cash, 0)
        out[f"est_pl_{key}"] = round(est_pl, 0)
        out[f"est_loss_{key}"] = round(est_loss, 0)

    out["risk_pct"] = risk_pct
    out["lot_size"] = lot
    return out