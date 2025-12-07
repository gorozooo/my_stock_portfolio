# aiapp/services/sizing_service.py
# -*- coding: utf-8 -*-
"""
AI Picks 用 ポジションサイズ計算サービス（短期×攻め・本気版）

- 楽天 / 松井 の 2段出力
- UserSetting.risk_pct と 各社倍率/ヘアカットを利用
- broker_summary.compute_broker_summaries() の結果に合わせて
    - 資産ベース: 現金残高 + 現物（特定）評価額
    - 予算ベース: 信用余力（概算）× credit_usage_pct（％）
- ATR / Entry / TP / SL を使って 1トレード許容損失からロットを計算
- 手数料・スリッページを見積もって
    - コスト負け
    - 利益がショボい
    - R が低すぎる
  などの理由で「見送り」を返す

ポリシーファイル（aiapp/policies/short_aggressive.yml）から読み込むもの：
- filters.min_net_profit_yen
- filters.min_reward_risk
- fees.commission_rate, fees.min_commission, fees.slippage_rate
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Tuple

from pathlib import Path

try:
    import yaml  # ポリシーファイル読み込み用
except Exception:  # PyYAML が無くても落ちないように
    yaml = None  # type: ignore

from django.db import transaction
from django.contrib.auth import get_user_model

from portfolio.models import UserSetting
from aiapp.services.broker_summary import compute_broker_summaries

# ------------------------------
# ポリシーデフォルト値
# ------------------------------

DEFAULT_MIN_NET_PROFIT_YEN = 1000.0
DEFAULT_MIN_REWARD_RISK = 1.0

DEFAULT_COMMISSION_RATE = 0.0005  # 0.05%
DEFAULT_MIN_COMMISSION = 100.0    # 最低手数料
DEFAULT_SLIPPAGE_RATE = 0.001     # 0.10%

_min_net_profit_yen = DEFAULT_MIN_NET_PROFIT_YEN
_min_reward_risk = DEFAULT_MIN_REWARD_RISK
_commission_rate = DEFAULT_COMMISSION_RATE
_min_commission = DEFAULT_MIN_COMMISSION
_slippage_rate = DEFAULT_SLIPPAGE_RATE

# aiapp/policies/short_aggressive.yml から上書き読み込み
try:
    if yaml is not None:  # PyYAML がある場合のみ
        policy_path = Path(__file__).resolve().parent.parent / "policies" / "short_aggressive.yml"
        if policy_path.exists():
            with policy_path.open("r", encoding="utf-8") as f:
                pdata = yaml.safe_load(f) or {}
            filters = pdata.get("filters") or {}
            fees = pdata.get("fees") or {}

            _min_net_profit_yen = float(filters.get("min_net_profit_yen", _min_net_profit_yen))
            _min_reward_risk = float(filters.get("min_reward_risk", _min_reward_risk))

            _commission_rate = float(fees.get("commission_rate", _commission_rate))
            _min_commission = float(fees.get("min_commission", _min_commission))
            _slippage_rate = float(fees.get("slippage_rate", _slippage_rate))
except Exception:
    # 読み込みに失敗してもデフォルトで動くようにする
    pass

# 実際に使う値（読み取り後）
MIN_NET_PROFIT_YEN = _min_net_profit_yen
MIN_REWARD_RISK = _min_reward_risk
COMMISSION_RATE = _commission_rate
MIN_COMMISSION = _min_commission
SLIPPAGE_RATE = _slippage_rate


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


def _load_user_setting(user) -> Tuple[float, float, float, float, float, float]:
    """
    UserSetting を取得し、リスク％・信用余力使用上限％と各社倍率/ヘアカットを返す。
    """
    us, _created = UserSetting.objects.get_or_create(
        user=user,
        defaults={
            "account_equity": 1_000_000,
            "risk_pct": 1.0,
            # credit_usage_pct フィールドの default と合わせておく
            "credit_usage_pct": 70.0,
        },
    )

    risk_pct = float(us.risk_pct or 1.0)
    credit_usage_pct = float(getattr(us, "credit_usage_pct", 70.0) or 70.0)

    # モデルのフィールド名は portfolio.models.UserSetting に合わせる
    rakuten_leverage = getattr(us, "leverage_rakuten", 2.90)
    rakuten_haircut = getattr(us, "haircut_rakuten", 0.30)
    matsui_leverage = getattr(us, "leverage_matsui", 2.80)
    matsui_haircut = getattr(us, "haircut_matsui", 0.00)

    return (
        risk_pct,
        credit_usage_pct,
        rakuten_leverage,
        rakuten_haircut,
        matsui_leverage,
        matsui_haircut,
    )


def _build_broker_envs(
    user,
    *,
    risk_pct: float,
    rakuten_leverage: float,
    rakuten_haircut: float,
    matsui_leverage: float,
    matsui_haircut: float,
) -> Dict[str, BrokerEnv]:
    """
    broker_summary.compute_broker_summaries() から
    楽天 / 松井 の現金・現物評価額・信用余力を引き出して、扱いやすい dict へ。
    """
    summaries = compute_broker_summaries(
        user=user,
        risk_pct=risk_pct,
        rakuten_leverage=rakuten_leverage,
        rakuten_haircut=rakuten_haircut,
        matsui_leverage=matsui_leverage,
        matsui_haircut=matsui_haircut,
        sbi_leverage=sbi_leverage,
        sbi_haircut=sbi_haircut,
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

    ポリシーの fees セクションから：
      - COMMISSION_RATE: 売買手数料レート
      - MIN_COMMISSION: 最低手数料
      - SLIPPAGE_RATE: スリッページ率
    """
    if entry <= 0 or qty <= 0:
        return 0.0
    notionals = entry * qty
    fee = max(MIN_COMMISSION, notionals * COMMISSION_RATE)
    slippage = notionals * SLIPPAGE_RATE
    return fee + slippage  # 片道（往復で×2想定）


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

    (
        risk_pct,
        credit_usage_pct,
        rakuten_leverage,
        rakuten_haircut,
        matsui_leverage,
        matsui_haircut,
    ) = _load_user_setting(user)

    lot = _lot_size_for(code)

    # ATR や価格が無効なら全て 0 で理由も「データ不足」
    if (
        atr is None
        or atr <= 0
        or last_price <= 0
        or entry is None
        or tp is None
        or sl is None
    ):
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
                "・楽天: 価格またはボラティリティ指標が不足しているため。",
                "・松井: 価格またはボラティリティ指標が不足しているため。",
            ],
        )

    envs = _build_broker_envs(
        user,
        risk_pct=risk_pct,
        rakuten_leverage=rakuten_leverage,
        rakuten_haircut=rakuten_haircut,
        matsui_leverage=matsui_leverage,
        matsui_haircut=matsui_haircut,
    )

    # 1株あたりの損失幅 / 利益幅
    loss_per_share = max(entry - sl, atr * 0.6)  # 損切り距離
    reward_per_share = max(tp - entry, 0.0)      # 利確距離（マイナスにはしない）

    result: Dict[str, Any] = {
        "risk_pct": risk_pct,
        "lot_size": lot,
    }

    # 各証券会社ごとの計算
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
            # 信用余力に credit_usage_pct（％）を掛けて、使ってよい上限を決める
            total_budget = max(env.credit_yoryoku, 0.0)
            budget = total_budget * (credit_usage_pct / 100.0)

            if risk_assets <= 0 or budget <= 0:
                qty = 0
                required_cash = 0.0
                est_pl = 0.0
                est_loss = 0.0
                reason_msg = "信用余力が 0 円のため。"
                reason_code = "no_budget"
            else:
                # 1トレードあたり許容損失
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

        # 結果を flat に格納
        result[f"qty_{short_key}"] = int(qty)
        result[f"required_cash_{short_key}"] = round(float(required_cash or 0.0), 0)
        result[f"est_pl_{short_key}"] = round(float(est_pl or 0.0), 0)
        result[f"est_loss_{short_key}"] = round(float(est_loss or 0.0), 0)
        result[f"reason_{short_key}_code"] = reason_code
        result[f"reason_{short_key}_msg"] = reason_msg

    # ★ どちらか一方でも 0株なら、その証券会社分の理由を bullets としてまとめる
    reasons_lines: List[str] = []
    for broker_label, short_key in (("楽天", "rakuten"), ("松井", "matsui")):
        msg = result.get(f"reason_{short_key}_msg") or ""
        qty = result.get(f"qty_{short_key}", 0)
        if qty == 0 and msg:
            reasons_lines.append(f"・{broker_label}: {msg}")

    result["reasons_text"] = reasons_lines or None
    return result