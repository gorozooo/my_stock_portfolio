# -*- coding: utf-8 -*-
"""
AI Picks 用 ポジションサイズ計算サービス（短期×攻め・本気版 / ポリシー駆動）

- 楽天 / 松井 の 2段出力
- UserSetting.risk_pct ＋ 各社倍率/ヘアカット
- broker_summary.compute_broker_summaries() の結果に合わせて:
    - 資産ベース: 現金残高 + 現物（特定）評価額
    - 予算ベース: 信用余力（概算）× ポリシーの credit_usage_pct（例: 70%）
- ATR / Entry / TP / SL を使って 1トレード許容損失からロットを計算
- 手数料・スリッページをポリシーから読み込み:
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
  "reasons_text": ["・楽天: ...", "・松井: ..."],
}
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Dict, List, Tuple

from django.conf import settings
from django.db import transaction
from django.contrib.auth import get_user_model

from portfolio.models import UserSetting
from aiapp.services.broker_summary import compute_broker_summaries

# YAML 読み込み
try:
    import yaml  # type: ignore
except ImportError:  # pragma: no cover
    yaml = None


# ------------------------------
# ポリシー関連
# ------------------------------

DEFAULT_POLICY_NAME = "short_aggressive"


def _policy_dir() -> str:
    """
    ポリシーファイル置き場のディレクトリ。
    例: BASE_DIR / "aiapp" / "policies"
    """
    return os.path.join(settings.BASE_DIR, "aiapp", "policies")


def _load_policy(policy_name: str) -> Dict[str, Any]:
    """
    YAML/JSON からポリシーを読み込む。
    - {name}.yml / {name}.yaml / {name}.json を順に探す
    - 見つからなければ組み込みデフォルトを返す
    """
    name = policy_name or DEFAULT_POLICY_NAME
    base = os.path.join(_policy_dir(), name)

    paths: List[str] = [
        base + ".yml",
        base + ".yaml",
        base + ".json",
    ]

    data: Dict[str, Any] = {}

    for path in paths:
        if os.path.isfile(path):
            ext = os.path.splitext(path)[1].lower()
            with open(path, "r", encoding="utf-8") as f:
                if ext in (".yml", ".yaml"):
                    if yaml is None:
                        raise RuntimeError(
                            "PyYAML がインストールされていないため、ポリシーYAMLを読み込めません。"
                        )
                    data = yaml.safe_load(f) or {}
                else:
                    import json

                    data = json.load(f) or {}
            break

    if not data:
        # 何も見つからない場合は組み込みデフォルト
        data = {
            "mode": "short_aggressive",
            "risk_pct": 1.0,
            "credit_usage_pct": 70.0,
            "lot_rule": {
                "etf_codes_prefix": ["13", "15"],
                "etf_lot": 1,
                "stock_lot": 100,
            },
            "filters": {
                "min_net_profit_yen": 1000.0,
                "min_reward_risk": 1.0,
                "allow_negative_pl": False,
            },
            "fees": {
                "commission_rate": 0.0005,
                "min_commission": 100.0,
                "slippage_rate": 0.001,
            },
            "entry_tp_sl": {
                "atr_sl_ratio": 0.60,
            },
        }

    # 安全側のデフォルト埋め
    filters = data.setdefault("filters", {})
    fees = data.setdefault("fees", {})
    lot_rule = data.setdefault("lot_rule", {})
    etsl = data.setdefault("entry_tp_sl", {})

    filters.setdefault("min_net_profit_yen", 1000.0)
    filters.setdefault("min_reward_risk", 1.0)
    filters.setdefault("allow_negative_pl", False)

    fees.setdefault("commission_rate", 0.0005)
    fees.setdefault("min_commission", 100.0)
    fees.setdefault("slippage_rate", 0.001)

    lot_rule.setdefault("etf_codes_prefix", ["13", "15"])
    lot_rule.setdefault("etf_lot", 1)
    lot_rule.setdefault("stock_lot", 100)

    etsl.setdefault("atr_sl_ratio", 0.60)

    if "credit_usage_pct" not in data:
        data["credit_usage_pct"] = 100.0

    if "risk_pct" not in data:
        data["risk_pct"] = 1.0

    return data


# ------------------------------
# 設定系
# ------------------------------


@dataclass
class BrokerEnv:
    label: str
    cash_yen: float
    stock_value: float
    credit_yoryoku: float  # UI上の「信用余力（概算）」フル値


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
    ※ risk_pct 自体はポリシー側が優先されるが、
      UserSetting の値をデフォルトとして利用する。
    """
    us, _created = UserSetting.objects.get_or_create(
        user=user,
        defaults={
            "account_equity": 1_000_000,
            "risk_pct": 1.0,
        },
    )
    risk_pct = float(us.risk_pct or 1.0)

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

    # UserSetting / ポリシー の両方から来る想定だが、ここでは値をそのまま受け取る。
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


def _lot_size_for(code: str, lot_rule: Dict[str, Any]) -> int:
    """
    ETF/ETN (13xx / 15xx) → 1株
    それ以外 → 100株
    （プレフィックスとロットはポリシーから取得）
    """
    prefixes = lot_rule.get("etf_codes_prefix", ["13", "15"])
    etf_lot = int(lot_rule.get("etf_lot", 1) or 1)
    stock_lot = int(lot_rule.get("stock_lot", 100) or 100)

    if any(code.startswith(p) for p in prefixes):
        return etf_lot
    return stock_lot


def _estimate_trading_cost(
    entry: float,
    qty: int,
    *,
    commission_rate: float,
    min_commission: float,
    slippage_rate: float,
) -> float:
    """
    信用取引のざっくりコスト見積もり（片道）。
    - 売買手数料: 約定代金の commission_rate（最低 min_commission 円）
    - スリッページ: 約定代金の slippage_rate をざっくり見積もる
    """
    if entry <= 0 or qty <= 0:
        return 0.0
    notionals = entry * qty
    fee = max(float(min_commission), notionals * float(commission_rate))
    slippage = notionals * float(slippage_rate)
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
    min_net_profit_yen: float,
    min_reward_risk: float,
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

    if net_profit < min_net_profit_yen:
        return f"純利益が {int(min_net_profit_yen):,} 円未満と小さすぎるため。"

    if rr < min_reward_risk:
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
    policy_name: str = DEFAULT_POLICY_NAME,
) -> Dict[str, Any]:
    """
    AI Picks 1銘柄分の数量と評価・理由を計算して返す。

    policy_name:
        "short_aggressive" などポリシーファイル名（拡張子なし）
        ※ 引数を追加しているがデフォルト付きなので既存呼び出しはそのまま動く。
    """
    # ポリシー読み込み
    policy = _load_policy(policy_name)
    filters = policy["filters"]
    fees = policy["fees"]
    lot_rule = policy["lot_rule"]
    etsl = policy["entry_tp_sl"]

    min_net_profit_yen = float(filters.get("min_net_profit_yen", 1000.0))
    min_reward_risk = float(filters.get("min_reward_risk", 1.0))
    allow_negative_pl = bool(filters.get("allow_negative_pl", False))

    commission_rate = float(fees.get("commission_rate", 0.0005))
    min_commission = float(fees.get("min_commission", 100.0))
    slippage_rate = float(fees.get("slippage_rate", 0.001))

    atr_sl_ratio = float(etsl.get("atr_sl_ratio", 0.60))

    credit_usage_pct = float(policy.get("credit_usage_pct", 100.0))
    policy_risk_pct = float(policy.get("risk_pct", 1.0))

    if user is None:
        user = _get_or_default_user()

    # UserSetting 側も読みつつ、risk_pct はポリシー優先
    us_risk_pct, _lr, _hr, _lm, _hm = _load_user_setting(user)
    risk_pct = policy_risk_pct or us_risk_pct or 1.0

    lot = _lot_size_for(code, lot_rule)

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

    # ブローカー環境（現金・現物取得額・信用余力フル値）
    envs = _build_broker_envs(user, risk_pct)

    # 1株あたりの損失幅 / 利益幅
    loss_per_share_raw = entry - sl
    loss_per_share = max(loss_per_share_raw, atr * atr_sl_ratio)  # 損切り距離
    reward_per_share = max(tp - entry, 0.0)  # 利確距離（マイナスにはしない）

    result: Dict[str, Any] = {
        "risk_pct": risk_pct,
        "lot_size": lot,
    }

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

            # 信用余力（UIのフル値）× ポリシーの usage_pct （例: 70%）
            budget_full = max(env.credit_yoryoku, 0.0)
            budget = budget_full * (credit_usage_pct / 100.0)

            if risk_assets <= 0 or budget <= 0:
                qty = 0
                required_cash = 0.0
                est_pl = 0.0
                est_loss = 0.0
                reason_msg = "信用余力が 0 円のため。"
                reason_code = "no_budget"
            else:
                # 1トレード許容リスク（円）
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
                    cost_round = _estimate_trading_cost(
                        entry,
                        test_qty,
                        commission_rate=commission_rate,
                        min_commission=min_commission,
                        slippage_rate=slippage_rate,
                    ) * 2
                    net_profit_test = gross_profit_test - cost_round
                    rr_test = (
                        (gross_profit_test / loss_value_test)
                        if loss_value_test > 0
                        else 0.0
                    )

                    reason_msg = _build_reason_for_zero(
                        broker_label,
                        qty=qty,
                        gross_profit=gross_profit_test,
                        net_profit=net_profit_test,
                        rr=rr_test,
                        budget=budget,
                        min_lot=lot,
                        loss_value=loss_per_share,
                        min_net_profit_yen=min_net_profit_yen,
                        min_reward_risk=min_reward_risk,
                    )
                    reason_code = "filtered"
                    required_cash = 0.0
                    est_pl = 0.0
                    est_loss = 0.0
                else:
                    # ここで一旦「プラス候補」として扱い、あとでフィルタ
                    gross_profit = reward_per_share * qty
                    loss_value = loss_per_share * qty
                    cost_round = _estimate_trading_cost(
                        entry,
                        qty,
                        commission_rate=commission_rate,
                        min_commission=min_commission,
                        slippage_rate=slippage_rate,
                    ) * 2
                    net_profit = gross_profit - cost_round
                    rr = (gross_profit / loss_value) if loss_value > 0 else 0.0

                    if (not allow_negative_pl) and net_profit <= 0:
                        qty = 0
                        required_cash = 0.0
                        est_pl = 0.0
                        est_loss = 0.0
                        reason_code = "net_profit_negative"
                        reason_msg = "手数料・スリッページを考慮すると純利益がマイナスになるため。"
                    elif net_profit < min_net_profit_yen:
                        qty = 0
                        required_cash = 0.0
                        est_pl = 0.0
                        est_loss = 0.0
                        reason_code = "profit_too_small"
                        reason_msg = (
                            f"純利益が {int(min_net_profit_yen):,} 円未満と小さすぎるため。"
                        )
                    elif rr < min_reward_risk:
                        qty = 0
                        required_cash = 0.0
                        est_pl = 0.0
                        est_loss = 0.0
                        reason_code = "rr_too_low"
                        reason_msg = (
                            f"利確幅に対して損切幅が大きく、R={rr:.2f} と基準未満のため。"
                        )
                    else:
                        # 最終的に採用
                        required_cash = entry * qty
                        est_pl = net_profit
                        est_loss = loss_value
                        reason_code = ""
                        reason_msg = ""

        # 結果を flat に格納
        result[f"qty_{short_key}"] = int(qty)
        result[f"required_cash_{short_key}"] = round(
            float(required_cash or 0.0), 0
        )
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