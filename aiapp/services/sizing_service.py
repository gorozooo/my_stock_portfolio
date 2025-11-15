# -*- coding: utf-8 -*-
"""
AI Picks 用 ポジションサイズ計算サービス（短期×攻め・本気版）

- 楽天 / 松井 の 2段出力
- UserSetting.risk_pct と 各社倍率/ヘアカットを利用
- broker_summary.compute_broker_summaries() の結果に合わせて
    - 資産ベース: 現金残高 + 現物（特定）評価額
    - 予算ベース: 信用余力（概算）
- ATR / Entry / TP / SL を使って 1トレード許容損失からロットを計算
- 手数料・スリッページを見積もって
    - コスト負け
    - 利益がショボい
    - R が低すぎる
  などの理由で「見送り」を返す

戻り値の dict 例:
{
  "risk_pct": 1.0,
  "lot_size": 100,
  "qty_rakuten": 100,
  "required_cash_rakuten": 123400,
  "est_pl_rakuten": 5400,
  "est_loss_rakuten": 3200,
  "reason_rakuten_code": "",
  "reason_rakuten_msg": "",
  "qty_matsui": 0,
  "required_cash_matsui": 0,
  "est_pl_matsui": 0,
  "est_loss_matsui": 0,
  "reason_matsui_code": "profit_too_small",
  "reason_matsui_msg": "純利益が 1,000 円未満と小さすぎるため。",
  "reasons_text": ["この銘柄は短期ルール上「見送り」です。", "・楽天: ...", "・松井: ..."],
}
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Tuple

from django.db import transaction
from django.contrib.auth import get_user_model

from portfolio.models import UserSetting
from aiapp.services.broker_summary import compute_broker_summaries


# ------------------------------
# 設定系
# ------------------------------

# 最低純利益（円）…これ未満なら「やっても意味が薄い」と判断
MIN_NET_PROFIT_YEN = 1000.0

# Reward / Risk (TPまでの幅 / SLまでの幅) の最低 R
MIN_REWARD_RISK = 1.0


@dataclass
class BrokerEnv:
    label: str
    cash_yen: float
    stock_value: float
    credit_yoryoku: float


def _get_or_default_user() -> Any:
    """
    cron など「ログインユーザーがいない」状況用に、
    とりあえず最初のユーザーを返すユーティリティ。
    （このアプリは実質 1ユーザー運用前提）
    """
    User = get_user_model()
    return User.objects.first()


def _load_user_setting(user) -> Tuple[float, float, float, float, float]:
    """
    UserSetting を取得し、リスク％と各社倍率/ヘアカットを返す。
    """
    us, _created = UserSetting.objects.get_or_create(
        user=user,
        defaults={
            "account_equity": 1_000_000,
            "risk_pct": 1.0,
        },
    )
    risk_pct = float(us.risk_pct or 1.0)

    # モデルのフィールド名は portfolio.models.UserSetting に合わせる
    rakuten_leverage = getattr(us, "leverage_rakuten", 2.90)
    rakuten_haircut = getattr(us, "haircut_rakuten", 0.30)
    matsui_leverage = getattr(us, "leverage_matsui", 2.80)
    matsui_haircut = getattr(us, "haircut_matsui", 0.00)

    return risk_pct, rakuten_leverage, rakuten_haircut, matsui_leverage, matsui_haircut


def _build_broker_envs(user, risk_pct: float) -> Dict[str, BrokerEnv]:
    """
    broker_summary.compute_broker_summaries() から
    楽天 / 松井 の現金・現物評価額・信用余力を引き出して、扱いやすい dict へ。
    """
    (
        _risk_pct,
        rakuten_leverage,
        rakuten_haircut,
        matsui_leverage,
        matsui_haircut,
    ) = _load_user_setting(user)

    risk_pct = float(risk_pct or _risk_pct or 1.0)

    summaries = compute_broker_summaries(
        user=user,
        risk_pct=risk_pct,
        rakuten_leverage=rakuten_leverage,
        rakuten_haircut=rakuten_haircut,
        matsui_leverage=matsui_leverage,
        matsui_haircut=matsui_haircut,
    )

    envs: Dict[str, BrokerEnv] = {}
    for s in summaries:
        label = getattr(s, "label", None)
        if not label:
            continue
        envs[label] = BrokerEnv(
            label=label,
            cash_yen=float(getattr(s, "cash_yen", 0) or 0),
            stock_value=float(getattr(s, "stock_acq_value", 0) or 0),
            credit_yoryoku=float(getattr(s, "credit_yoryoku", 0) or 0),
        )
    return envs


def _lot_size_for(code: str) -> int:
    """
    ETF/ETN (13xx / 15xx) → 1株
    それ以外 → 100株
    """
    if code.startswith("13") or code.startswith("15"):
        return 1
    return 100


def _estimate_trading_cost(entry: float, qty: int) -> float:
    """
    信用取引のざっくりコスト見積もり（片道）。
    - 売買手数料: 約定代金の 0.05%（最低 100円）イメージ
    - スリッページ: 約定代金の 0.10% をざっくり見積もる
    """
    if entry <= 0 or qty <= 0:
        return 0.0
    notionals = entry * qty
    fee = max(100.0, notionals * 0.0005)  # 0.05% or 100円
    slippage = notionals * 0.001          # 0.10%
    return fee + slippage                 # 片道（往復で×2想定）


def _build_reason_for_zero(
    label: str,
    *,
    qty: int,
    gross_profit: float,
    net_profit: float,
    rr: float,
    budget: float,
    min_lot: int,
    loss_value: float,
) -> str:
    """
    qty=0 になったときの「なぜゼロなのか」を細かく判定して日本語メッセージを返す。
    label: "楽天" / "松井"
    """
    if budget <= 0:
        return "信用余力が 0 円のため。"

    if budget < min_lot * loss_value:
        return "信用余力が最小単元に対しても不足しているため。"

    if gross_profit <= 0:
        return "TPまで到達しても想定利益がプラスにならないため。"

    if net_profit <= 0:
        return "手数料・スリッページを考慮すると純利益がマイナスになるため。"

    if net_profit < MIN_NET_PROFIT_YEN:
        return f"純利益が {int(MIN_NET_PROFIT_YEN):,} 円未満と小さすぎるため。"

    if rr < MIN_REWARD_RISK:
        return f"利確幅に対して損切幅が大きく、R={rr:.2f} と基準未満のため。"

    # ここまで来て qty=0 はほぼ無いはずだが、念のため
    return "リスク％から計算した必要株数が最小単元に満たないため。"


# ------------------------------
# メイン API
# ------------------------------

@transaction.atomic
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
    AI Picks 1銘柄分の数量と評価・理由を計算して返す。
    """
    if user is None:
        user = _get_or_default_user()

    risk_pct, _lr, _hr, _lm, _hm = _load_user_setting(user)
    lot = _lot_size_for(code)

    # ATR や価格が無効なら全て 0 で理由も「データ不足」
    if atr is None or atr <= 0 or last_price <= 0 or entry is None or tp is None or sl is None:
        return dict(
            qty_rakuten=0,
            required_cash_rakuten=0,
            est_pl_rakuten=0,
            est_loss_rakuten=0,
            reason_rakuten_code="invalid_data",
            reason_rakuten_msg="価格またはボラティリティ指標が不足しているため。",
            qty_matsui=0,
            required_cash_matsui=0,
            est_pl_matsui=0,
            est_loss_matsui=0,
            reason_matsui_code="invalid_data",
            reason_matsui_msg="価格またはボラティリティ指標が不足しているため。",
            risk_pct=risk_pct,
            lot_size=lot,
            reasons_text=[
                "この銘柄は短期ルール上「見送り」です。",
                "・楽天: 価格またはボラティリティ指標が不足しているため。",
                "・松井: 価格またはボラティリティ指標が不足しているため。",
            ],
        )

    envs = _build_broker_envs(user, risk_pct)

    # 1株あたりの損失幅 / 利益幅
    loss_per_share = max(entry - sl, atr * 0.6)   # 損切り距離
    reward_per_share = max(tp - entry, 0.0)       # 利確距離（マイナスにはしない）

    result: Dict[str, Any] = {
        "risk_pct": risk_pct,
        "lot_size": lot,
    }

    any_positive = False  # ★ 最終的にどちらかが >0 かどうかを見る

    for broker_label, short_key in (("楽天", "rakuten"), ("松井", "matsui")):
        env = envs.get(broker_label)
        if env is None:
            qty = 0
            required_cash = 0.0
            est_pl = 0.0
            est_loss = 0.0
            reason_msg = "該当する証券口座の情報が見つからないため。"
            reason_code = "no_account"
        else:
            risk_assets = max(env.cash_yen + env.stock_value, 0.0)
            budget = max(env.credit_yoryoku, 0.0)

            if risk_assets <= 0 or budget <= 0:
                qty = 0
                required_cash = 0.0
                est_pl = 0.0
                est_loss = 0.0
                reason_msg = "信用余力が 0 円のため。"
                reason_code = "no_budget"
            else:
                risk_value = risk_assets * (risk_pct / 100.0)

                if loss_per_share <= 0:
                    max_by_risk = 0
                else:
                    max_by_risk = int(risk_value / loss_per_share // lot * lot)

                max_by_budget = int(budget / max(entry, last_price) // lot * lot)

                qty = min(max_by_risk, max_by_budget)

                if qty < lot:
                    qty = 0

                if qty <= 0:
                    # 「仮に最小ロットで入った場合」で理由を判定
                    test_qty = lot
                    gross_profit_test = reward_per_share * test_qty
                    loss_value_test = loss_per_share * test_qty
                    cost_round = _estimate_trading_cost(entry, test_qty) * 2
                    net_profit_test = gross_profit_test - cost_round
                    rr_test = (gross_profit_test / loss_value_test) if loss_value_test > 0 else 0.0

                    reason_msg = _build_reason_for_zero(
                        broker_label,
                        qty=qty,
                        gross_profit=gross_profit_test,
                        net_profit=net_profit_test,
                        rr=rr_test,
                        budget=budget,
                        min_lot=lot,
                        loss_value=loss_per_share,
                    )
                    reason_code = "filtered"
                    required_cash = 0.0
                    est_pl = 0.0
                    est_loss = 0.0
                else:
                    # ここで一旦「プラス候補」として扱い、あとでフィルタ
                    gross_profit = reward_per_share * qty
                    loss_value = loss_per_share * qty
                    cost_round = _estimate_trading_cost(entry, qty) * 2
                    net_profit = gross_profit - cost_round
                    rr = (gross_profit / loss_value) if loss_value > 0 else 0.0

                    if net_profit <= 0:
                        qty = 0
                        required_cash = 0.0
                        est_pl = 0.0
                        est_loss = 0.0
                        reason_code = "net_profit_negative"
                        reason_msg = "手数料・スリッページを考慮すると純利益がマイナスになるため。"
                    elif net_profit < MIN_NET_PROFIT_YEN:
                        qty = 0
                        required_cash = 0.0
                        est_pl = 0.0
                        est_loss = 0.0
                        reason_code = "profit_too_small"
                        reason_msg = f"純利益が {int(MIN_NET_PROFIT_YEN):,} 円未満と小さすぎるため。"
                    elif rr < MIN_REWARD_RISK:
                        qty = 0
                        required_cash = 0.0
                        est_pl = 0.0
                        est_loss = 0.0
                        reason_code = "rr_too_low"
                        reason_msg = f"利確幅に対して損切幅が大きく、R={rr:.2f} と基準未満のため。"
                    else:
                        # 最終的に採用
                        required_cash = entry * qty
                        est_pl = net_profit
                        est_loss = loss_value
                        reason_code = ""
                        reason_msg = ""
                        any_positive = True  # ★ ここでだけ True にする

        # 結果を flat に格納
        result[f"qty_{short_key}"] = int(qty)
        result[f"required_cash_{short_key}"] = round(float(required_cash or 0.0), 0)
        result[f"est_pl_{short_key}"] = round(float(est_pl or 0.0), 0)
        result[f"est_loss_{short_key}"] = round(float(est_loss or 0.0), 0)
        result[f"reason_{short_key}_code"] = reason_code
        result[f"reason_{short_key}_msg"] = reason_msg

    # ★ 最終的な数量を見て「両方 0 なら reasons_text を付ける」
    reasons_lines: List[str] = []
    if not any_positive:
        reasons_lines.append("この銘柄は短期ルール上「見送り」です。")
        for broker_label, short_key in (("楽天", "rakuten"), ("松井", "matsui")):
            msg = result.get(f"reason_{short_key}_msg") or ""
            if msg:
                reasons_lines.append(f"・{broker_label}: {msg}")

    result["reasons_text"] = reasons_lines or None
    return result