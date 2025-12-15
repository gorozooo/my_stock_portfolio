# aiapp/services/sizing_service.py
# -*- coding: utf-8 -*-
"""
AI Picks 用 ポジションサイズ計算サービス（短期×攻め・本気版）

追加（今回の主目的）：
- risk_pct を ML で “自動調整” できるようにする
  → 期待値（ml_ev）/ 勝率（ml_p_win）/ SL先確率（ml_tp_first_probs）で上下
  → Entry/TP/SL の自動調整と “同じ判断軸” でサイズも動かす

重要：
- 呼び出し互換を壊さない（既存の picks_build 呼び出しはそのまま動く）
- MLが無い/欠損なら従来の risk_pct をそのまま使う（運用を止めない）
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
            "credit_usage_pct": 70.0,
        },
    )

    risk_pct = float(us.risk_pct or 1.0)
    credit_usage_pct = float(getattr(us, "credit_usage_pct", 70.0) or 70.0)

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


def _estimate_trading_cost(entry: float, qty: int) -> float:
    """
    信用取引のざっくりコスト見積もり（片道）。
    """
    if entry <= 0 or qty <= 0:
        return 0.0
    notionals = entry * qty
    fee = max(MIN_COMMISSION, notionals * COMMISSION_RATE)
    slippage = notionals * SLIPPAGE_RATE
    return fee + slippage  # 片道（往復で×2想定）


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _safe_float(x: Any) -> float:
    try:
        v = float(x)
        if v != v:  # NaN
            return 0.0
        if v == float("inf") or v == float("-inf"):
            return 0.0
        return v
    except Exception:
        return 0.0


def _normalize_probs(d: Any) -> Dict[str, float]:
    out = {"tp_first": 0.0, "sl_first": 0.0, "none": 0.0}
    if not isinstance(d, dict):
        return out
    for k in ("tp_first", "sl_first", "none"):
        out[k] = _clamp(_safe_float(d.get(k)), 0.0, 1.0)
    s = out["tp_first"] + out["sl_first"] + out["none"]
    if s <= 0:
        return out
    return {k: float(v / s) for k, v in out.items()}


def _risk_pct_auto_adjust(
    *,
    risk_pct_base: float,
    ml_ev: Any = None,
    ml_p_win: Any = None,
    ml_tp_first_probs: Any = None,
) -> Tuple[float, Dict[str, Any]]:
    """
    MLから risk_pct を自動調整する。

    直感：
    - ev / p_win が高いほど “少しだけ” 上げてよい
    - ただし SL先確率が高いならガッツリ下げる（逆行しやすい）
    - いきなり暴れないように係数を強くクランプする
    """
    base = float(risk_pct_base or 1.0)

    ev = _safe_float(ml_ev)
    pwin = _safe_float(ml_p_win)
    probs = _normalize_probs(ml_tp_first_probs)
    p_sl = float(probs.get("sl_first", 0.0))

    # MLが無いなら調整しない
    has_ml = (ev > 0) or (pwin > 0) or (isinstance(ml_tp_first_probs, dict))
    if not has_ml:
        return base, {"k_risk": 1.0, "p_sl_first": p_sl, "has_ml": False}

    # 基本係数（控えめ・暴れない）
    # 例：
    #   ev: 0.8を基準、上なら増、下なら減
    #   pwin: 0.5を基準、上なら少し増
    #   p_sl: 0.0〜1.0 で強く減点（先に逆行しやすい）
    k = 0.6 + 0.8 * (ev - 0.8) + 0.3 * (pwin - 0.5) - 0.7 * p_sl
    k = _clamp(k, 0.35, 1.60)

    eff = base * k

    # 極端に上がりすぎないように、絶対値でもクランプ
    # (ユーザー設定が1%なら最大でも2.5%程度、0.5%なら最大でも1.25%程度)
    eff = _clamp(eff, base * 0.35, base * 2.50)

    meta = {
        "has_ml": True,
        "k_risk": float(k),
        "p_sl_first": float(p_sl),
        "ml_ev": float(ev),
        "ml_p_win": float(pwin),
    }
    return float(eff), meta


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
    # ★ 追加：MLでrisk%を動かす（互換のため引数追加は末尾、既存呼び出しは壊れない）
    ml_ev: Any = None,
    ml_p_win: Any = None,
    ml_tp_first_probs: Any = None,
) -> Dict[str, Any]:
    """
    AI Picks 1銘柄分の数量と評価・理由を計算して返す。
    """
    if user is None:
        user = _get_or_default_user()

    (
        risk_pct_base,
        credit_usage_pct,
        rakuten_leverage,
        rakuten_haircut,
        matsui_leverage,
        matsui_haircut,
        sbi_leverage,
        sbi_haircut,
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
        msg = "価格またはボラティリティ指標が不足しているため。"
        return dict(
            qty_rakuten=0,
            required_cash_rakuten=0,
            est_pl_rakuten=0,
            est_loss_rakuten=0,
            reason_rakuten_code="invalid_data",
            reason_rakuten_msg=msg,
            qty_matsui=0,
            required_cash_matsui=0,
            est_pl_matsui=0,
            est_loss_matsui=0,
            reason_matsui_code="invalid_data",
            reason_matsui_msg=msg,
            qty_sbi=0,
            required_cash_sbi=0,
            est_pl_sbi=0,
            est_loss_sbi=0,
            reason_sbi_code="invalid_data",
            reason_sbi_msg=msg,
            # base/eff を返す（UIやログで確認できる）
            risk_pct=float(risk_pct_base),
            risk_pct_effective=float(risk_pct_base),
            lot_size=lot,
            reasons_text=[
                f"・楽天: {msg}",
                f"・松井: {msg}",
                f"・SBI: {msg}",
            ],
        )

    # ★ MLで risk_pct を自動調整
    risk_pct_eff, risk_meta = _risk_pct_auto_adjust(
        risk_pct_base=float(risk_pct_base),
        ml_ev=ml_ev,
        ml_p_win=ml_p_win,
        ml_tp_first_probs=ml_tp_first_probs,
    )

    envs = _build_broker_envs(
        user,
        risk_pct=float(risk_pct_base),
        rakuten_leverage=rakuten_leverage,
        rakuten_haircut=rakuten_haircut,
        matsui_leverage=matsui_leverage,
        matsui_haircut=matsui_haircut,
        sbi_leverage=sbi_leverage,
        sbi_haircut=sbi_haircut,
    )

    # 1株あたりの損失幅 / 利益幅
    # SLが浅すぎるとサイズが暴れるので最低幅は atr*0.6 を残す（元の思想を維持）
    loss_per_share = max(entry - sl, atr * 0.6)
    reward_per_share = max(tp - entry, 0.0)

    result: Dict[str, Any] = {
        "risk_pct": float(risk_pct_base),
        "risk_pct_effective": float(risk_pct_eff),
        "risk_pct_meta": risk_meta,
        "lot_size": lot,
    }

    # 各証券会社ごとの計算
    for broker_label, short_key in (("楽天", "rakuten"), ("松井", "matsui"), ("SBI", "sbi")):
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
                # ★ 自動調整後の risk_pct を使う
                risk_value = risk_assets * (float(risk_pct_eff) / 100.0)

                if loss_per_share <= 0:
                    max_by_risk = 0
                else:
                    max_by_risk = int(risk_value / loss_per_share // lot * lot)

                max_by_budget = int(budget / max(entry, last_price) // lot * lot)

                qty = min(max_by_risk, max_by_budget)

                if qty < lot:
                    qty = 0

                if qty <= 0:
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
                        required_cash = entry * qty
                        est_pl = net_profit
                        est_loss = loss_value
                        reason_code = ""
                        reason_msg = ""

        result[f"qty_{short_key}"] = int(qty)
        result[f"required_cash_{short_key}"] = round(float(required_cash or 0.0), 0)
        result[f"est_pl_{short_key}"] = round(float(est_pl or 0.0), 0)
        result[f"est_loss_{short_key}"] = round(float(est_loss or 0.0), 0)
        result[f"reason_{short_key}_code"] = reason_code
        result[f"reason_{short_key}_msg"] = reason_msg

    reasons_lines: List[str] = []
    for broker_label, short_key in (("楽天", "rakuten"), ("松井", "matsui"), ("SBI", "sbi")):
        msg = result.get(f"reason_{short_key}_msg") or ""
        qty = result.get(f"qty_{short_key}", 0)
        if qty == 0 and msg:
            reasons_lines.append(f"・{broker_label}: {msg}")

    result["reasons_text"] = reasons_lines or None
    return result