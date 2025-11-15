# aiapp/services/sizing_service.py
# -*- coding: utf-8 -*-
"""
AI Picks 数量・必要資金・損益を証券会社別に算出するサービス（理由付き）
 - 楽天、松井の2段出力（qty, required_cash, est_pl, est_loss）
 - UserSetting.risk_pct / leverage_* / haircut_* を使用
 - ATR・Entry/TP/SL から R値・期待利益・コストを算出
 - 「Rが低い」「利益ショボい」「コスト負け」などの理由をテキストで返す
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Tuple

from django.db.models import Sum
from django.contrib.auth import get_user_model

from portfolio.models import (
    BrokerAccount,
    CashLedger,
    Holding,
    UserSetting,
)
from aiapp.services.broker_summary import compute_broker_summaries


# ------------------------------
# 設定値（あとでポリシー/YAML化予定）
# ------------------------------

# 最低R値（期待利益 / 想定損失）
MIN_R = 1.2

# 最低「純利益」（期待利益 − コスト）
MIN_NET_PROFIT_YEN = 3000

# 手数料・スリッページのざっくり係数（あとで調整可）
BROKER_COST_PARAM: Dict[str, Dict[str, float]] = {
    # ラベルは BrokerSummary の label と合わせる
    "楽天": {
        "fee_rate": 0.00088,   # 片道手数料率（概算）
        "min_fee": 0.0,        # 最低手数料（円）
        "slip_rate": 0.0005,   # 片道スリッページ率（概算）
    },
    "松井": {
        "fee_rate": 0.00088,
        "min_fee": 0.0,
        "slip_rate": 0.0005,
    },
}


@dataclass
class BrokerRiskBase:
    """1証券会社分のリスク計算用データ"""
    label: str
    risk_assets: float       # リスク％を掛けるベース資産（現金＋現物）
    available_budget: float  # 実際に使える枠（信用余力）


# ------------------------------
# ユーティリティ
# ------------------------------

def _lot_size_for(code: str) -> int:
    """
    ETF/ETN (13xx / 15xx) → 1株
    日本株 → 100株
    """
    if code.startswith("13") or code.startswith("15"):
        return 1
    return 100


def _load_user_setting(user) -> UserSetting:
    us, _ = UserSetting.objects.get_or_create(
        user=user,
        defaults=dict(
            account_equity=1_000_000,
            risk_pct=1.0,
        ),
    )
    # 後方互換：属性が無い場合は既定値で埋める
    if not hasattr(us, "leverage_rakuten"):
        us.leverage_rakuten = 2.9
    if not hasattr(us, "haircut_rakuten"):
        us.haircut_rakuten = 0.30
    if not hasattr(us, "leverage_matsui"):
        us.leverage_matsui = 2.8
    if not hasattr(us, "haircut_matsui"):
        us.haircut_matsui = 0.0
    return us


def _build_risk_base(user) -> Tuple[Dict[str, BrokerRiskBase], float]:
    """
    BrokerSummary を使って「リスクベース資産」と「実際に使える枠」を取得する。
    戻り値: ({label: BrokerRiskBase}, risk_pct)
    """
    us = _load_user_setting(user)
    risk_pct = float(us.risk_pct or 1.0)

    brokers = compute_broker_summaries(
        user=user,
        risk_pct=risk_pct,
        rakuten_leverage=float(getattr(us, "leverage_rakuten", 2.9)),
        rakuten_haircut=float(getattr(us, "haircut_rakuten", 0.30)),
        matsui_leverage=float(getattr(us, "leverage_matsui", 2.8)),
        matsui_haircut=float(getattr(us, "haircut_matsui", 0.0)),
    )

    out: Dict[str, BrokerRiskBase] = {}
    for b in brokers:
        # broker_summary.BrokerNumbers を想定（属性アクセス）
        cash = float(getattr(b, "cash_yen", 0.0) or 0.0)
        stock = float(
            getattr(b, "stock_eval", getattr(b, "stock_acq_value", 0.0)) or 0.0
        )
        yoryoku = float(getattr(b, "credit_yoryoku", 0.0) or 0.0)
        label = str(getattr(b, "label", "") or "")

        out[label] = BrokerRiskBase(
            label=label,
            risk_assets=cash + stock,
            available_budget=yoryoku,
        )

    return out, risk_pct


def _estimate_trade_cost(broker_label: str, notional: float) -> float:
    """
    片道コスト(手数料+スリッページ)をざっくり計算し、往復分(×2)で返す。
    """
    if notional <= 0:
        return 0.0
    p = BROKER_COST_PARAM.get(broker_label, {})
    fee_rate = float(p.get("fee_rate", 0.00088))
    min_fee = float(p.get("min_fee", 0.0))
    slip_rate = float(p.get("slip_rate", 0.0005))

    fee_one = max(min_fee, notional * fee_rate)
    slip_one = notional * slip_rate
    return 2.0 * (fee_one + slip_one)


# ------------------------------
# メインロジック
# ------------------------------

def compute_position_sizing(
    user,
    *,
    code: str,
    last_price: float,
    atr: float,
    entry: float,
    tp: float,
    sl: float,
) -> Dict[str, Any]:
    """
    AI Picks 1銘柄分の数量を楽天・松井の2段で返す（理由テキスト付き）

    戻り値（主なキー）:
        qty_rakuten, qty_matsui
        required_cash_rakuten, required_cash_matsui
        est_pl_rakuten, est_pl_matsui
        est_loss_rakuten, est_loss_matsui
        reasons_rakuten: List[str]
        reasons_matsui:  List[str]
        risk_pct, lot_size
    """
    lot = _lot_size_for(str(code))

    # ATR・価格が無効なら即 0 判定
    if not atr or atr <= 0 or not last_price or last_price <= 0:
        return dict(
            qty_rakuten=0, qty_matsui=0,
            required_cash_rakuten=0, required_cash_matsui=0,
            est_pl_rakuten=0, est_pl_matsui=0,
            est_loss_rakuten=0, est_loss_matsui=0,
            reasons_rakuten=["価格/ATR が取得できなかったため見送り。"],
            reasons_matsui=["価格/ATR が取得できなかったため見送り。"],
            risk_pct=_load_user_setting(user).risk_pct,
            lot_size=lot,
        )

    broker_map, risk_pct = _build_risk_base(user)

    out: Dict[str, Any] = {}
    for broker_label in ("楽天", "松井"):
        rb = broker_map.get(broker_label)
        reasons: List[str] = []

        if rb is None:
            # その証券会社に口座が無い
            qty = required_cash = est_pl = est_loss = 0
            reasons.append("対象の証券口座が見つからなかったため 0株。")
        else:
            risk_assets = float(rb.risk_assets or 0.0)
            budget = float(rb.available_budget or 0.0)

            if risk_assets <= 0 or budget <= 0:
                qty = required_cash = est_pl = est_loss = 0
                reasons.append("資産または信用余力が 0 のため見送り。")
            else:
                # 1トレードあたりの許容損失
                risk_value = risk_assets * (risk_pct / 100.0)

                # 損切幅（円）: ATR の 0.6倍
                loss_per_share = atr * 0.6
                if loss_per_share <= 0:
                    qty = required_cash = est_pl = est_loss = 0
                    reasons.append("損切幅が計算できなかったため見送り。")
                else:
                    # リスクベースでの最大株数
                    qty_risk = int((risk_value / loss_per_share) // lot * lot)

                    # 枠（信用余力）ベースの最大株数
                    # Entry価格で建てたときに budget を超えないように制限
                    max_qty_budget = int((budget / entry) // lot * lot)

                    qty = min(qty_risk, max_qty_budget)

                    if qty <= 0:
                        required_cash = est_pl = est_loss = 0
                        reasons.append(
                            f"リスクと信用余力を考慮すると最小単元（{lot}株）でも建てられないため 0株。"
                        )
                    else:
                        required_cash = qty * entry

                        # 想定利益/損失
                        # 利益：Entry→TP
                        gross_profit = max(0.0, (tp - entry)) * qty
                        # 損失：Entry→SL
                        est_loss = max(0.0, (entry - sl)) * qty

                        # コスト見積もり
                        notional = qty * entry
                        cost_total = _estimate_trade_cost(broker_label, notional)

                        net_profit = gross_profit - cost_total
                        R = (gross_profit / est_loss) if est_loss > 0 else 0.0

                        # 理由の積み上げ
                        reasons.append(
                            f"リスクベース許容損失 ≈ {risk_value:,.0f}円 / 損切幅 {loss_per_share:,.1f}円。"
                        )
                        reasons.append(
                            f"建てる場合の想定利益 ≈ {gross_profit:,.0f}円 / 想定損失 ≈ {est_loss:,.0f}円 (R ≈ {R:.2f})。"
                        )
                        reasons.append(
                            f"推定コスト（手数料＋スリッページ往復） ≈ {cost_total:,.0f}円。"
                        )

                        # フィルタ条件
                        rejected = False
                        if R < MIN_R:
                            reasons.append(
                                f"R値 {R:.2f} < しきい値 {MIN_R:.2f} のため見送り候補。"
                            )
                            rejected = True

                        if net_profit <= 0:
                            reasons.append(
                                f"コスト込み純利益 {net_profit:,.0f}円 ≤ 0円 のため見送り候補。"
                            )
                            rejected = True
                        elif net_profit < MIN_NET_PROFIT_YEN:
                            reasons.append(
                                f"コスト込み純利益 {net_profit:,.0f}円 < 最低純利益 {MIN_NET_PROFIT_YEN:,.0f}円 のため見送り候補。"
                            )
                            rejected = True

                        if rejected:
                            # 結局見送り
                            qty = 0
                            required_cash = 0
                            gross_profit = 0
                            est_loss = 0
                            reasons.append("総合判定：条件を満たさないため 0株。")
                        else:
                            reasons.append(
                                "総合判定：R・純利益・枠とも条件を満たすため、この株数で提案。"
                            )

                        est_pl = gross_profit

        key = "rakuten" if broker_label == "楽天" else "matsui"
        out[f"qty_{key}"] = int(qty)
        out[f"required_cash_{key}"] = round(required_cash, 0)
        out[f"est_pl_{key}"] = round(est_pl, 0)
        out[f"est_loss_{key}"] = round(est_loss, 0)
        out[f"reasons_{key}"] = reasons

    out["risk_pct"] = float(risk_pct)
    out["lot_size"] = lot
    return out