# -*- coding: utf-8 -*-
"""
AI Picks 用 ポジションサイズ計算サービス（短期 × 攻め・本気版）

- 楽天 / 松井 それぞれについて
    qty_rakuten / qty_matsui
    required_cash_rakuten / required_cash_matsui
    est_pl_rakuten / est_pl_matsui
    est_loss_rakuten / est_loss_matsui
  を返す。

- 口座側の前提:
    * AI設定(UserSetting) に
        - risk_pct
        - leverage_rakuten / haircut_rakuten
        - leverage_matsui / haircut_matsui
      が入っている。
    * 証券サマリと同じロジックで
      compute_broker_summaries() を呼び出し、
      「信用余力（概算）」を取得する。

- ロジック概要:
    1) 楽天/松井ごとに
         risk_assets = 現金 + 現物評価額 × (1 - ヘアカット)
       をリスクベースとする。
    2) 1トレード許容損失 = risk_assets × (risk_pct / 100)
    3) 損切幅(1株あたり) = entry - SL
       （両方ない / 不正なら ATR × 0.6 にフォールバック）
    4) 「リスク的に許される最大株数」と
       「信用余力で買える最大株数」を両方計算し、
       その min() を実際の株数にする。
       → 必要資金が信用余力を超えることはない。

- ロット:
    * 13xx / 15xx → ETF とみなし 1株
    * その他 → 100株
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Tuple, Mapping, Optional, List

from django.db import transaction

from portfolio.models import UserSetting
from aiapp.services.broker_summary import compute_broker_summaries


# ---- 定数（暫定：将来AI設定から変えられるようにする） ----------------------

# 手数料・スプレッド込みの安全係数
#   必要資金 = qty × price × COST_MULTIPLIER
COST_MULTIPLIER: float = 1.001  # 0.1% だけ多めに見る（将来調整）


# ---- ユーティリティ ------------------------------------------------------


def _lot_size_for(code: str) -> int:
    """
    ETF/ETN (13xx / 15xx) → 1株
    その他の日本株 → 100株
    """
    if not code:
        return 100
    if code.startswith("13") or code.startswith("15"):
        return 1
    return 100


@dataclass
class RiskEnv:
    """数量計算に必要な環境一式（キャッシュ）"""
    risk_pct: float
    rakuten_leverage: float
    rakuten_haircut: float
    matsui_leverage: float
    matsui_haircut: float

    @classmethod
    def from_user(cls, user) -> "RiskEnv":
        us, _ = UserSetting.objects.get_or_create(
            user=user,
            defaults=dict(
                account_equity=1_000_000,
                risk_pct=1.0,
                leverage_rakuten=2.90,
                haircut_rakuten=0.30,
                leverage_matsui=2.80,
                haircut_matsui=0.00,
            ),
        )
        return cls(
            risk_pct=float(us.risk_pct or 1.0),
            rakuten_leverage=float(getattr(us, "leverage_rakuten", 2.90)),
            rakuten_haircut=float(getattr(us, "haircut_rakuten", 0.30)),
            matsui_leverage=float(getattr(us, "leverage_matsui", 2.80)),
            matsui_haircut=float(getattr(us, "haircut_matsui", 0.00)),
        )


@dataclass
class BrokerNumbers:
    """broker_summary から拾う必要最小限の値だけをまとめたもの"""
    label: str
    cash_yen: float
    stock_acq_value: float
    credit_yoryoku: float


def _load_broker_numbers(user, env: RiskEnv) -> Dict[str, BrokerNumbers]:
    """
    証券サマリサービスから楽天/松井の数値をロードして
    label → BrokerNumbers で引けるようにする。
    """
    # 設定画面と同じ引数で呼ぶ（サマリと完全一致させる）
    raw_list = compute_broker_summaries(
        user=user,
        risk_pct=env.risk_pct,
        rakuten_leverage=env.rakuten_leverage,
        rakuten_haircut=env.rakuten_haircut,
        matsui_leverage=env.matsui_leverage,
        matsui_haircut=env.matsui_haircut,
    )

    out: Dict[str, BrokerNumbers] = {}
    for b in raw_list:
        # broker_summary 側の dataclass を想定：属性名ベースで読む
        label = str(getattr(b, "label", ""))
        if not label:
            continue
        out[label] = BrokerNumbers(
            label=label,
            cash_yen=float(getattr(b, "cash_yen", 0.0) or 0.0),
            stock_acq_value=float(getattr(b, "stock_acq_value", 0.0) or 0.0),
            credit_yoryoku=float(getattr(b, "credit_yoryoku", 0.0) or 0.0),
        )
    return out


def _risk_assets_and_budget(
    label: str,
    env: RiskEnv,
    brokers: Mapping[str, BrokerNumbers],
) -> Tuple[float, float]:
    """
    指定ブローカーについて
      - risk_assets（リスクベースの資産）
      - available_budget（信用余力：サマリと一致）
    を返す。
    """
    b = brokers.get(label)
    if b is None:
        return 0.0, 0.0

    if label == "楽天":
        haircut = env.rakuten_haircut
    elif label == "松井":
        haircut = env.matsui_haircut
    else:
        haircut = 0.0

    cash = float(b.cash_yen or 0.0)
    stock = float(b.stock_acq_value or 0.0)

    # 現物にはヘアカットを適用したうえでリスクベースに乗せる
    risk_assets = cash + max(stock * (1.0 - haircut), 0.0)
    available_budget = float(b.credit_yoryoku or 0.0)

    return risk_assets, available_budget


def _loss_per_share(entry: Optional[float], sl: Optional[float], atr: float) -> float:
    """
    1株あたりの損切幅を決める。
      - entry と sl が両方あり、かつ entry > sl なら entry - sl
      - そうでなければ ATR × 0.6 にフォールバック
    """
    try:
        e = float(entry) if entry is not None else None
        s = float(sl) if sl is not None else None
        a = float(atr or 0.0)
    except Exception:
        e = s = None
        a = float(atr or 0.0)

    if e is not None and s is not None and e > s:
        width = e - s
    else:
        width = a * 0.6

    if width <= 0:
        return 0.0
    return width


def _est_pl(entry: Optional[float], tp: Optional[float], atr: float, qty: int) -> float:
    """
    想定利益（ざっくり）
      - entry / TP があれば (TP - entry) × qty
      - 無ければ ATR × 0.8 × qty
    """
    try:
        e = float(entry) if entry is not None else None
        t = float(tp) if tp is not None else None
    except Exception:
        e = t = None

    if e is not None and t is not None and t > e:
        gain = (t - e) * qty
    else:
        gain = float(atr or 0.0) * 0.8 * qty
    return max(gain, 0.0)


# ---- メイン: ポジションサイズ計算 -----------------------------------------


@transaction.non_atomic_requests
def compute_position_sizing(
    user,
    code: str,
    last_price: float,
    atr: float,
    *,
    entry: Optional[float] = None,
    tp: Optional[float] = None,
    sl: Optional[float] = None,
) -> Dict[str, Any]:
    """
    AI Picks 1銘柄分のポジションサイズを計算する。

    戻り値(dict):
        qty_rakuten, qty_matsui
        required_cash_rakuten, required_cash_matsui
        est_pl_rakuten, est_pl_matsui
        est_loss_rakuten, est_loss_matsui
        risk_pct, lot_size
    """
    price = float(last_price or 0.0)
    atr_val = float(atr or 0.0)

    lot = _lot_size_for(str(code or ""))
    env = RiskEnv.from_user(user)
    brokers = _load_broker_numbers(user, env)

    # ATR や価格が 0 に近い場合は何も勧めない
    if price <= 0 or atr_val <= 0:
        return {
            "qty_rakuten": 0,
            "qty_matsui": 0,
            "required_cash_rakuten": 0,
            "required_cash_matsui": 0,
            "est_pl_rakuten": 0,
            "est_pl_matsui": 0,
            "est_loss_rakuten": 0,
            "est_loss_matsui": 0,
            "risk_pct": env.risk_pct,
            "lot_size": lot,
        }

    # 損切幅（1株あたり）
    loss_per_share = _loss_per_share(entry, sl, atr_val)
    if loss_per_share <= 0:
        # まともな損切幅が出ない場合は 0 提案
        return {
            "qty_rakuten": 0,
            "qty_matsui": 0,
            "required_cash_rakuten": 0,
            "required_cash_matsui": 0,
            "est_pl_rakuten": 0,
            "est_pl_matsui": 0,
            "est_loss_rakuten": 0,
            "est_loss_matsui": 0,
            "risk_pct": env.risk_pct,
            "lot_size": lot,
        }

    # 実際の発注価格としては「Entry があれば Entry、なければ現在値」を採用
    try:
        price_for_cash = float(entry) if entry is not None else price
    except Exception:
        price_for_cash = price

    result: Dict[str, Any] = {
        "risk_pct": env.risk_pct,
        "lot_size": lot,
    }

    for label, key in (("楽天", "rakuten"), ("松井", "matsui")):
        risk_assets, available_budget = _risk_assets_and_budget(label, env, brokers)

        if risk_assets <= 0 or available_budget <= 0:
            qty = 0
        else:
            # 1トレードあたり許容できる損失金額
            risk_value = risk_assets * (env.risk_pct / 100.0)

            # リスク的に許される最大株数
            max_qty_risk = int((risk_value / loss_per_share) // lot * lot)

            # 「信用余力」で買える最大株数（手数料込み）
            per_share_cost = price_for_cash * COST_MULTIPLIER
            if per_share_cost <= 0:
                max_qty_budget = 0
            else:
                max_qty_budget = int((available_budget / per_share_cost) // lot * lot)

            qty = min(max_qty_risk, max_qty_budget)
            if qty < lot:
                qty = 0  # ワンショットすら建てられないなら 0

        required_cash = int(round(qty * price_for_cash * COST_MULTIPLIER))
        est_loss = int(round(qty * loss_per_share))
        est_pl = int(round(_est_pl(entry, tp, atr_val, qty)))

        result[f"qty_{key}"] = int(qty)
        result[f"required_cash_{key}"] = required_cash
        result[f"est_pl_{key}"] = est_pl
        result[f"est_loss_{key}"] = est_loss

    return result