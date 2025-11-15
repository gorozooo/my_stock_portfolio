# aiapp/services/sizing_service.py
# -*- coding: utf-8 -*-
"""
AI Picks 数量・必要資金・損益を証券会社別に算出するサービス

ゴール：
- 楽天 / 松井 を 2段表示（qty / 必要資金 / 想定利益 / 想定損失）
- UserSetting.risk_pct と 証券会社サマリ（信用余力など）を利用
- ATR ベースで Entry / TP / SL が決まっている前提で、
    1. リスク％ → 許容損失(円) → ロット数を計算
    2. 信用余力（概算）を超えないように数量を制限
    3. 手数料＋スリッページを概算
    4. TP 到達時の利益と比較して
        - コスト負け or 利益ショボい or R が低い → 0株
        - 条件を満たす → この株数でGO
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, Optional, Tuple

from django.db.models import Sum

from portfolio.models import BrokerAccount, CashLedger, Holding, UserSetting
from aiapp.services.broker_summary import compute_broker_summaries


# ===== パラメータ（短期 × 攻め 用の暫定値） ==============================

# 最低 R（利益 / 損失）。これ未満なら見送り
MIN_R = 1.2

# 「手数料＋スリッページ」に対して、利益が何倍あれば許容するか
COST_MULTIPLIER = 1.5  # 利益がコストの 1.5倍未満なら見送り

# 1トレードあたりの最低純利益（円）。これ未満なら見送り
MIN_NET_PROFIT_YEN = 3000.0

# 手数料・スリッページの簡易モデル（往復合計の比率）
# ※ 将来は証券会社やモード別に YAML/JSON ポリシーへ移行する前提
BROKER_COST_MODEL = {
    "楽天": dict(round_trip_rate=0.0015),  # 0.15% をざっくり想定
    "松井": dict(round_trip_rate=0.0015),
}


# ===== ユーティリティ =====================================================

def _lot_size_for(code: str) -> int:
    """
    ETF/ETN (13xx / 15xx) → 1株
    日本株 → 100株
    """
    code = str(code or "")
    if code.startswith("13") or code.startswith("15"):
        return 1
    return 100


def _get_user_setting(user) -> Tuple[float, float, float, float, float]:
    """
    UserSetting から
        risk_pct,
        leverage_rakuten, haircut_rakuten,
        leverage_matsui, haircut_matsui
    を取得（無ければデフォルト）
    """
    try:
        us = UserSetting.objects.get(user=user)
    except UserSetting.DoesNotExist:
        # デフォルト値（あなたの運用に合わせた初期値）
        return 1.0, 2.90, 0.30, 2.80, 0.00

    risk_pct = float(getattr(us, "risk_pct", 1.0) or 1.0)

    leverage_rakuten = float(getattr(us, "leverage_rakuten", 2.90) or 2.90)
    haircut_rakuten = float(getattr(us, "haircut_rakuten", 0.30) or 0.30)

    leverage_matsui = float(getattr(us, "leverage_matsui", 2.80) or 2.80)
    haircut_matsui = float(getattr(us, "haircut_matsui", 0.00) or 0.00)

    return risk_pct, leverage_rakuten, haircut_rakuten, leverage_matsui, haircut_matsui


@dataclass
class RiskBase:
    """リスク計算に使うベース情報"""
    broker_label: str
    risk_assets: float       # リスク資産ベース（現金＋現物）
    available_budget: float  # 新規で使える概算枠（信用余力など）


def _build_risk_bases(
    user,
    brokers: Optional[Iterable[Any]] = None,
) -> Dict[str, RiskBase]:
    """
    証券会社ごとの
        - risk_assets   = 現金残高 + 現物取得額
        - available_budget = 信用余力（概算）
    を作る。

    brokers が None の場合は broker_summary サービスを呼んで揃える。
    """
    risk_pct, lr, hr, lm, hm = _get_user_setting(user)

    # broker_summary から取得（settings 画面と同じソース）
    if brokers is None:
        brokers = compute_broker_summaries(
            user=user,
            risk_pct=risk_pct,
            rakuten_leverage=lr,
            rakuten_haircut=hr,
            matsui_leverage=lm,
            matsui_haircut=hm,
        )

    bases: Dict[str, RiskBase] = {}

    for b in brokers:
        # BrokerNumbers dataclass or dict を想定した防御的アクセス
        label = getattr(b, "label", None)
        if label is None and isinstance(b, dict):
            label = b.get("label")
        if not label:
            continue

        cash = getattr(b, "cash_yen", None)
        if cash is None and isinstance(b, dict):
            cash = b.get("cash_yen")
        cash = float(cash or 0.0)

        stock_acq = getattr(b, "stock_acq_value", None)
        if stock_acq is None and isinstance(b, dict):
            stock_acq = b.get("stock_acq_value")
        stock_acq = float(stock_acq or 0.0)

        credit_yoryoku = getattr(b, "credit_yoryoku", None)
        if credit_yoryoku is None and isinstance(b, dict):
            credit_yoryoku = b.get("credit_yoryoku")
        credit_yoryoku = float(credit_yoryoku or 0.0)

        risk_assets = max(0.0, cash + stock_acq)
        available_budget = max(0.0, credit_yoryoku)

        bases[label] = RiskBase(
            broker_label=label,
            risk_assets=risk_assets,
            available_budget=available_budget,
        )

    return bases


def _estimate_cost(broker_label: str, qty: int, price: float) -> float:
    """
    ざっくりした往復コスト（手数料＋スリッページ）を見積もる。
    - 将来は証券会社別・モード別・金額帯別にポリシー化する前提。
    """
    if qty <= 0 or price <= 0:
        return 0.0
    notional = float(qty) * float(price)
    model = BROKER_COST_MODEL.get(broker_label, {"round_trip_rate": 0.0015})
    rate = float(model.get("round_trip_rate", 0.0015) or 0.0)
    cost = notional * rate
    # あまりに小さい約定の手数料下限をざっくり見るイメージ
    if cost < 100.0:
        cost = 100.0
    return cost


def _compute_qty_for_broker(
    *,
    broker_label: str,
    risk_base: Optional[RiskBase],
    risk_pct: float,
    lot_size: int,
    last_price: float,
    atr: float,
    entry: Optional[float],
    tp: Optional[float],
    sl: Optional[float],
) -> Tuple[int, float, float, float]:
    """
    1証券会社ぶんの数量・必要資金・想定利益・想定損失を計算。
    条件を満たさなければ qty=0 にする。
    """
    # 前提条件チェック
    if (
        risk_base is None
        or risk_base.risk_assets <= 0
        or risk_base.available_budget <= 0
        or atr is None
        or atr <= 0
        or last_price <= 0
    ):
        return 0, 0.0, 0.0, 0.0

    lot = max(1, int(lot_size))

    # 1トレードあたりの許容損失（円）
    risk_value = float(risk_base.risk_assets) * float(risk_pct) / 100.0

    # 損切幅（1株あたり）
    # - SL があれば entry - SL をベースに
    # - 無い場合は ATR×0.6 を fallback
    if entry is not None and sl is not None:
        loss_per_share = float(entry) - float(sl)
    else:
        loss_per_share = 0.0
    if loss_per_share <= 0:
        loss_per_share = float(atr) * 0.6

    if loss_per_share <= 0:
        return 0, 0.0, 0.0, 0.0

    # リスクから計算される理論最大株数
    raw_qty = int(risk_value / loss_per_share)
    if raw_qty <= 0:
        return 0, 0.0, 0.0, 0.0

    # 単元調整
    qty = (raw_qty // lot) * lot

    # 枠（信用余力）から引ける最大株数でも制限
    max_by_budget = int(risk_base.available_budget / float(last_price))
    max_by_budget = (max_by_budget // lot) * lot
    if max_by_budget <= 0:
        return 0, 0.0, 0.0, 0.0

    qty = min(qty, max_by_budget)
    if qty <= 0:
        return 0, 0.0, 0.0, 0.0

    # 必要資金（概算）
    base_price = float(entry or last_price)
    required_cash = float(qty) * base_price

    # 想定利益・損失（1株あたり）
    if entry is not None and tp is not None:
        gain_per_share = float(tp) - float(entry)
    else:
        gain_per_share = float(atr) * 0.8

    if gain_per_share <= 0:
        # 利益見込みがゼロ以下なら見送り
        return 0, 0.0, 0.0, 0.0

    est_profit = gain_per_share * qty
    est_loss = loss_per_share * qty

    # コスト見積もり
    cost = _estimate_cost(broker_label, qty, base_price)

    # R（リワード / リスク）
    R = est_profit / est_loss if est_loss > 0 else 0.0

    # ===== ふるい落とし条件 =====
    # 1) コスト負け
    if est_profit <= cost * COST_MULTIPLIER:
        return 0, 0.0, 0.0, 0.0

    # 2) 純利益がショボい
    if est_profit <= MIN_NET_PROFIT_YEN:
        return 0, 0.0, 0.0, 0.0

    # 3) Rが低すぎる
    if R < MIN_R:
        return 0, 0.0, 0.0, 0.0

    # 条件クリア → 採用
    return qty, required_cash, est_profit, est_loss


# ===== 公開関数 ===========================================================

def compute_position_sizing(
    *,
    user,
    code: str,
    last_price: float,
    atr: float,
    entry: Optional[float] = None,
    tp: Optional[float] = None,
    sl: Optional[float] = None,
    brokers: Optional[Iterable[Any]] = None,
) -> Dict[str, Any]:
    """
    AI Picks 1銘柄分の数量を 楽天 / 松井 の 2段で返す。

    戻り値：
        qty_rakuten, qty_matsui
        required_cash_rakuten, required_cash_matsui
        est_pl_rakuten, est_pl_matsui
        est_loss_rakuten, est_loss_matsui
        risk_pct, lot_size
    """
    lot_size = _lot_size_for(code)

    # UserSetting 取得
    risk_pct, lr, hr, lm, hm = _get_user_setting(user)

    # リスクベース（証券会社サマリと同じロジック）
    risk_bases = _build_risk_bases(user, brokers=brokers)

    # 各証券会社ごとに数量計算
    out: Dict[str, Any] = {}

    for label, key in [("楽天", "rakuten"), ("松井", "matsui")]:
        base = risk_bases.get(label)

        qty, required_cash, est_pl, est_loss = _compute_qty_for_broker(
            broker_label=label,
            risk_base=base,
            risk_pct=risk_pct,
            lot_size=lot_size,
            last_price=float(last_price or 0.0),
            atr=float(atr or 0.0),
            entry=entry,
            tp=tp,
            sl=sl,
        )

        out[f"qty_{key}"] = int(qty)
        out[f"required_cash_{key}"] = float(round(required_cash, 0))
        out[f"est_pl_{key}"] = float(round(est_pl, 0))
        out[f"est_loss_{key}"] = float(round(est_loss, 0))

    out["risk_pct"] = float(risk_pct)
    out["lot_size"] = int(lot_size)

    return out