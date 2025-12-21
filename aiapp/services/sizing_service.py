# aiapp/services/sizing_service.py
# -*- coding: utf-8 -*-
"""
AI Picks 用 ポジションサイズ計算サービス（短期×攻め・本気版）

- 楽天 / 松井 / SBI の 3段出力（SBIも含める。勝手に除外しない）
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

★重要（今回の修正）:
- ポリシーを import 時に固定しない。
  compute_position_sizing() の呼び出し毎に YAML を読み直して “常に最新” を使う。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Tuple, Optional
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


def _policy_path() -> Path:
    return Path(__file__).resolve().parent.parent / "policies" / "short_aggressive.yml"


def _load_policy_values() -> Tuple[float, float, float, float, float]:
    """
    short_aggressive.yml を毎回読み直して、使う閾値を返す。
    戻り値:
      (min_net_profit_yen, min_reward_risk, commission_rate, min_commission, slippage_rate)
    """
    min_net_profit_yen = float(DEFAULT_MIN_NET_PROFIT_YEN)
    min_reward_risk = float(DEFAULT_MIN_REWARD_RISK)
    commission_rate = float(DEFAULT_COMMISSION_RATE)
    min_commission = float(DEFAULT_MIN_COMMISSION)
    slippage_rate = float(DEFAULT_SLIPPAGE_RATE)

    try:
        if yaml is None:
            return min_net_profit_yen, min_reward_risk, commission_rate, min_commission, slippage_rate
        p = _policy_path()
        if not p.exists():
            return min_net_profit_yen, min_reward_risk, commission_rate, min_commission, slippage_rate

        with p.open("r", encoding="utf-8") as f:
            pdata = yaml.safe_load(f) or {}

        filters = pdata.get("filters") or {}
        fees = pdata.get("fees") or {}

        try:
            min_net_profit_yen = float(filters.get("min_net_profit_yen", min_net_profit_yen))
        except Exception:
            pass
        try:
            min_reward_risk = float(filters.get("min_reward_risk", min_reward_risk))
        except Exception:
            pass
        try:
            commission_rate = float(fees.get("commission_rate", commission_rate))
        except Exception:
            pass
        try:
            min_commission = float(fees.get("min_commission", min_commission))
        except Exception:
            pass
        try:
            slippage_rate = float(fees.get("slippage_rate", slippage_rate))
        except Exception:
            pass

        return min_net_profit_yen, min_reward_risk, commission_rate, min_commission, slippage_rate
    except Exception:
        return min_net_profit_yen, min_reward_risk, commission_rate, min_commission, slippage_rate


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


def _load_user_setting(user) -> Tuple[float, float, float, float, float, float, float, float]:
    """
    UserSetting を取得し、リスク％・信用余力使用上限％と
    各社倍率/ヘアカット（楽天・松井・SBI）を返す。
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
    sbi_leverage = getattr(us, "leverage_sbi", 2.80)
    sbi_haircut = getattr(us, "haircut_sbi", 0.30)

    return (
        risk_pct,
        credit_usage_pct,
        rakuten_leverage,
        rakuten_haircut,
        matsui_leverage,
        matsui_haircut,
        sbi_leverage,
        sbi_haircut,
    )


def _build_broker_envs(
    user,
    *,
    risk_pct: float,
    rakuten_leverage: float,
    rakuten_haircut: float,
    matsui_leverage: float,
    matsui_haircut: float,
    sbi_leverage: float,
    sbi_haircut: float,
) -> Dict[str, BrokerEnv]:
    """
    broker_summary.compute_broker_summaries() から
    楽天 / 松井 / SBI の現金・現物評価額・信用余力を引き出して、扱いやすい dict へ。
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


def _estimate_trading_cost(entry: float, qty: int, *, commission_rate: float, min_commission: float, slippage_rate: float) -> float:
    """
    信用取引のざっくりコスト見積もり（片道）。
    """
    if entry <= 0 or qty <= 0:
        return 0.0
    notionals = entry * qty
    fee = max(float(min_commission), notionals * float(commission_rate))
    slippage = notionals * float(slippage_rate)
    return fee + slippage  # 片道（往復で×2想定）


def _safe_div(a: float, b: float) -> float:
    if b is None or b == 0:
        return 0.0
    return float(a) / float(b)


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
    label: "楽天" / "松井" / "SBI"
    """
    if budget <= 0:
        return "信用余力が 0 円のため。"

    if budget < min_lot * loss_value:
        return "信用余力が最小単元に対しても不足しているため。"

    if gross_profit <= 0:
        return "TPまで到達しても想定利益がプラスにならないため。"

    if net_profit <= 0:
        return "手数料・スリッページを考慮すると純利益がマイナスになるため。"

    if net_profit < float(min_net_profit_yen):
        return f"純利益が {int(float(min_net_profit_yen)):,} 円未満と小さすぎるため。"

    if rr < float(min_reward_risk):
        return f"利確幅に対して損切幅が大きく、R={rr:.2f} と基準未満のため。"

    # ここまで来て qty=0 はほぼ無いはずだが、念のため
    return "リスク％から計算した必要株数が最小単元に満たないため。"


def _normalize_prob(p: Optional[float]) -> Optional[float]:
    if p is None:
        return None
    try:
        v = float(p)
    except Exception:
        return None
    if v != v:  # NaN
        return None
    # 0..1 に丸め
    if v < 0.0:
        v = 0.0
    if v > 1.0:
        v = 1.0
    return v


def _derive_psl(
    p_tp_first: Optional[float],
    p_sl_first: Optional[float],
    p_none: Optional[float],
) -> Optional[float]:
    """
    pSL が未指定のときの安全な推定。
    - pSL が与えられていればそれを使う
    - ない場合は 1 - pTP - pNone を採用（0..1に丸め）
    """
    p_tp = _normalize_prob(p_tp_first)
    p_sl = _normalize_prob(p_sl_first)
    p_n = _normalize_prob(p_none)

    if p_sl is not None:
        return p_sl

    if p_tp is None:
        return None

    if p_n is None:
        v = 1.0 - p_tp
    else:
        v = 1.0 - p_tp - p_n

    if v < 0.0:
        v = 0.0
    if v > 1.0:
        v = 1.0
    return v


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
    *,
    p_tp_first: Optional[float] = None,
    p_sl_first: Optional[float] = None,
    p_none: Optional[float] = None,
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
        sbi_leverage,
        sbi_haircut,
    ) = _load_user_setting(user)

    # ★毎回ポリシーを読み直す（最新反映）
    MIN_NET_PROFIT_YEN, MIN_REWARD_RISK, COMMISSION_RATE, MIN_COMMISSION, SLIPPAGE_RATE = _load_policy_values()

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
        msg = "価格またはボラティリティ指標が不足しているため。"
        return dict(
            qty_rakuten=0,
            required_cash_rakuten=0,
            est_pl_rakuten=0,
            est_loss_rakuten=0,
            ev_net_rakuten=None,
            rr_net_rakuten=None,
            ev_true_rakuten=None,
            reason_rakuten_code="invalid_data",
            reason_rakuten_msg=msg,

            qty_matsui=0,
            required_cash_matsui=0,
            est_pl_matsui=0,
            est_loss_matsui=0,
            ev_net_matsui=None,
            rr_net_matsui=None,
            ev_true_matsui=None,
            reason_matsui_code="invalid_data",
            reason_matsui_msg=msg,

            qty_sbi=0,
            required_cash_sbi=0,
            est_pl_sbi=0,
            est_loss_sbi=0,
            ev_net_sbi=None,
            rr_net_sbi=None,
            ev_true_sbi=None,
            reason_sbi_code="invalid_data",
            reason_sbi_msg=msg,

            risk_pct=risk_pct,
            lot_size=lot,
            reasons_text=[
                f"・楽天: {msg}",
                f"・松井: {msg}",
                f"・SBI: {msg}",
            ],
        )

    envs = _build_broker_envs(
        user,
        risk_pct=risk_pct,
        rakuten_leverage=rakuten_leverage,
        rakuten_haircut=rakuten_haircut,
        matsui_leverage=matsui_leverage,
        matsui_haircut=matsui_haircut,
        sbi_leverage=sbi_leverage,
        sbi_haircut=sbi_haircut,
    )

    # 1株あたりの損失幅 / 利益幅
    loss_per_share = max(entry - sl, atr * 0.6)  # 損切り距離（最低保障）
    reward_per_share = max(tp - entry, 0.0)      # 利確距離（マイナスにはしない）

    # pTP/pSL（本命EV用）
    p_tp = _normalize_prob(p_tp_first)
    p_sl = _derive_psl(p_tp_first, p_sl_first, p_none)

    result: Dict[str, Any] = {
        "risk_pct": risk_pct,
        "lot_size": lot,
    }

    # 各証券会社ごとの計算
    for broker_label, short_key in (("楽天", "rakuten"), ("松井", "matsui"), ("SBI", "sbi")):
        env = envs.get(broker_label)
        qty = 0
        required_cash = 0.0
        est_pl = 0.0
        est_loss = 0.0
        reason_msg = ""
        reason_code = ""
        ev_net = None
        rr_net = None
        ev_true = None

        if env is None:
            reason_msg = "該当する証券口座の情報が見つからないため。"
            reason_code = "no_account"
        else:
            risk_assets = max(env.cash_yen + env.stock_value, 0.0)
            total_budget = max(env.credit_yoryoku, 0.0)
            budget = total_budget * (credit_usage_pct / 100.0)

            if risk_assets <= 0 or budget <= 0:
                reason_msg = "信用余力が 0 円のため。"
                reason_code = "no_budget"
            else:
                # 1トレードあたり許容損失（円）
                risk_value = risk_assets * (risk_pct / 100.0)

                # リスク上限での最大株数
                if loss_per_share <= 0:
                    max_by_risk = 0
                else:
                    max_by_risk = int(risk_value / loss_per_share // lot * lot)

                # 予算上限での最大株数
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
                        entry, test_qty,
                        commission_rate=COMMISSION_RATE,
                        min_commission=MIN_COMMISSION,
                        slippage_rate=SLIPPAGE_RATE,
                    ) * 2
                    net_profit_test = gross_profit_test - cost_round
                    rr_test = _safe_div(gross_profit_test, loss_value_test)

                    reason_msg = _build_reason_for_zero(
                        broker_label,
                        qty=qty,
                        gross_profit=gross_profit_test,
                        net_profit=net_profit_test,
                        rr=rr_test,
                        budget=budget,
                        min_lot=lot,
                        loss_value=loss_per_share,
                        min_net_profit_yen=MIN_NET_PROFIT_YEN,
                        min_reward_risk=MIN_REWARD_RISK,
                    )
                    reason_code = "filtered"
                else:
                    # 採用候補としてPL計算（手数料込み）
                    gross_profit = reward_per_share * qty
                    loss_value = loss_per_share * qty
                    cost_round = _estimate_trading_cost(
                        entry, qty,
                        commission_rate=COMMISSION_RATE,
                        min_commission=MIN_COMMISSION,
                        slippage_rate=SLIPPAGE_RATE,
                    ) * 2
                    net_profit = gross_profit - cost_round
                    rr = _safe_div(gross_profit, loss_value)
                    rr_net_val = _safe_div(net_profit, loss_value)

                    # EV_net（今は rr_net と同義）
                    ev_net_val = rr_net_val

                    # ★本命：pTP を混ぜた EV_true（R換算）
                    # EV_TRUE_R = pTP * RR_net - pSL * 1.0
                    if p_tp is not None and p_sl is not None:
                        ev_true_val = (p_tp * rr_net_val) - (p_sl * 1.0)
                    else:
                        ev_true_val = None

                    if net_profit <= 0:
                        qty = 0
                        reason_code = "net_profit_negative"
                        reason_msg = "手数料・スリッページを考慮すると純利益がマイナスになるため。"
                    elif net_profit < MIN_NET_PROFIT_YEN:
                        qty = 0
                        reason_code = "profit_too_small"
                        reason_msg = f"純利益が {int(MIN_NET_PROFIT_YEN):,} 円未満と小さすぎるため。"
                    elif rr < MIN_REWARD_RISK:
                        qty = 0
                        reason_code = "rr_too_low"
                        reason_msg = f"利確幅に対して損切幅が大きく、R={rr:.2f} と基準未満のため。"
                    else:
                        # 最終採用
                        required_cash = entry * qty
                        est_pl = net_profit
                        est_loss = loss_value
                        ev_net = ev_net_val
                        rr_net = rr_net_val
                        ev_true = ev_true_val

        # 結果を flat に格納
        result[f"qty_{short_key}"] = int(qty)
        result[f"required_cash_{short_key}"] = round(float(required_cash or 0.0), 0)
        result[f"est_pl_{short_key}"] = round(float(est_pl or 0.0), 0)
        result[f"est_loss_{short_key}"] = round(float(est_loss or 0.0), 0)

        result[f"ev_net_{short_key}"] = (float(ev_net) if ev_net is not None else None)
        result[f"rr_net_{short_key}"] = (float(rr_net) if rr_net is not None else None)
        result[f"ev_true_{short_key}"] = (float(ev_true) if ev_true is not None else None)

        result[f"reason_{short_key}_code"] = reason_code
        result[f"reason_{short_key}_msg"] = reason_msg

    # ★ どちらか一方でも 0株なら、その証券会社分の理由を bullets としてまとめる
    reasons_lines: List[str] = []
    for broker_label, short_key in (("楽天", "rakuten"), ("松井", "matsui"), ("SBI", "sbi")):
        msg = result.get(f"reason_{short_key}_msg") or ""
        qtyv = result.get(f"qty_{short_key}", 0)
        if qtyv == 0 and msg:
            reasons_lines.append(f"・{broker_label}: {msg}")

    result["reasons_text"] = reasons_lines or None
    return result