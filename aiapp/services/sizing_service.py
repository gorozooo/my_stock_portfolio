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

★追加（C対応：PRO 1口座統一）
- 仮想口座（PRO）を 1本作り、qty_pro / required_cash_pro / est_pl_pro / est_loss_pro を出力
- 採用・順位・学習の主軸を PRO に寄せられるようにする（既存3社出力は保持）
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

_min_net_profit_yen = DEFAULT_MIN_NET_PROFIT_YEN
_min_reward_risk = DEFAULT_MIN_REWARD_RISK
_commission_rate = DEFAULT_COMMISSION_RATE
_min_commission = DEFAULT_MIN_COMMISSION
_slippage_rate = DEFAULT_SLIPPAGE_RATE

# C対応：PRO仮想口座のデフォルト（プロっぽい“標準設定”）
# ※ここは将来 policy に移すのが理想だが、まずは壊さず導入を優先
DEFAULT_PRO_LEVERAGE = 2.80
DEFAULT_PRO_HAIRCUT = 0.30

_pro_leverage = DEFAULT_PRO_LEVERAGE
_pro_haircut = DEFAULT_PRO_HAIRCUT

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

            # 任意（あれば）：PRO設定（無ければデフォルトでOK）
            # 例:
            # pro:
            #   leverage: 2.8
            #   haircut: 0.3
            pro = pdata.get("pro") or {}
            if isinstance(pro, dict):
                _pro_leverage = float(pro.get("leverage", _pro_leverage))
                _pro_haircut = float(pro.get("haircut", _pro_haircut))
except Exception:
    # 読み込みに失敗してもデフォルトで動くようにする
    pass

# 実際に使う値（読み取り後）
MIN_NET_PROFIT_YEN = _min_net_profit_yen
MIN_REWARD_RISK = _min_reward_risk
COMMISSION_RATE = _commission_rate
MIN_COMMISSION = _min_commission
SLIPPAGE_RATE = _slippage_rate

PRO_LEVERAGE = _pro_leverage
PRO_HAIRCUT = _pro_haircut


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


def _load_user_setting(user) -> Tuple[float, float, float, float, float, float, float, float, float]:
    """
    UserSetting を取得し、リスク％・信用余力使用上限％と
    各社倍率/ヘアカット（楽天・松井・SBI）を返す。
    さらに PRO用の account_equity も返す（仮想口座の資産ベース）
    """
    us, _created = UserSetting.objects.get_or_create(
        user=user,
        defaults={
            "account_equity": 1_000_000,
            "risk_pct": 1.0,
            "credit_usage_pct": 70.0,
        },
    )

    account_equity = float(getattr(us, "account_equity", 1_000_000) or 1_000_000)
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
        account_equity,
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


def _build_pro_env(account_equity: float, credit_usage_pct: float) -> BrokerEnv:
    """
    C対応：PRO仮想口座（1口座統一）
    - risk_assets: account_equity を資産ベースとする
    - budget: (account_equity * leverage * (1 - haircut)) を信用余力の概算として使い、
              さらに credit_usage_pct を掛ける（実際の計算は後段）
    """
    eq = max(float(account_equity or 0.0), 0.0)

    # 信用余力の“プロ風”概算（標準）
    credit_base = eq * float(PRO_LEVERAGE) * (1.0 - float(PRO_HAIRCUT))
    credit_base = max(credit_base, 0.0)

    # credit_usage_pct は後で掛けるが、ここでは yoryoku として保持
    return BrokerEnv(
        label="PRO",
        cash_yen=eq,         # 便宜上、現金として持たせる（risk_assets計算で使う）
        stock_value=0.0,
        credit_yoryoku=credit_base,
    )


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
) -> str:
    """
    qty=0 になったときの「なぜゼロなのか」を細かく判定して日本語メッセージを返す。
    label: "楽天" / "松井" / "SBI" / "PRO"
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
    *,
    p_tp_first: Optional[float] = None,
    p_sl_first: Optional[float] = None,
    p_none: Optional[float] = None,
) -> Dict[str, Any]:
    """
    AI Picks 1銘柄分の数量と評価・理由を計算して返す。

    返す値（抜粋）:
      - rr_net_<x>: 純利益/想定損失（R換算）
      - ev_true_<x>: 期待値や確率混合などは将来拡張の余地として残す（現状は None のことが多い）
      - ★C対応：qty_pro / required_cash_pro / est_pl_pro / est_loss_pro を追加
    """
    if user is None:
        user = _get_or_default_user()

    (
        account_equity,
        risk_pct,
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
            # --- PRO ---
            qty_pro=0,
            required_cash_pro=0,
            est_pl_pro=0,
            est_loss_pro=0,
            ev_net_pro=None,
            rr_net_pro=None,
            ev_true_pro=None,
            reason_pro_code="invalid_data",
            reason_pro_msg=msg,

            # --- 楽天 ---
            qty_rakuten=0,
            required_cash_rakuten=0,
            est_pl_rakuten=0,
            est_loss_rakuten=0,
            ev_net_rakuten=None,
            rr_net_rakuten=None,
            ev_true_rakuten=None,
            reason_rakuten_code="invalid_data",
            reason_rakuten_msg=msg,

            # --- 松井 ---
            qty_matsui=0,
            required_cash_matsui=0,
            est_pl_matsui=0,
            est_loss_matsui=0,
            ev_net_matsui=None,
            rr_net_matsui=None,
            ev_true_matsui=None,
            reason_matsui_code="invalid_data",
            reason_matsui_msg=msg,

            # --- SBI ---
            qty_sbi=0,
            required_cash_sbi=0,
            est_pl_sbi=0,
            est_loss_sbi=0,
            ev_net_sbi=None,
            rr_net_sbi=None,
            ev_true_sbi=None,
            reason_sbi_code="invalid_data",
            reason_sbi_msg=msg,

            account_equity=account_equity,
            risk_pct=risk_pct,
            credit_usage_pct=credit_usage_pct,
            lot_size=lot,
            reasons_text=[
                f"・PRO: {msg}",
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

    # C対応：PRO仮想口座
    pro_env = _build_pro_env(account_equity=account_equity, credit_usage_pct=credit_usage_pct)

    # 1株あたりの損失幅 / 利益幅
    loss_per_share = max(entry - sl, atr * 0.6)  # 損切り距離（最低保障）
    reward_per_share = max(tp - entry, 0.0)      # 利確距離（マイナスにはしない）

    result: Dict[str, Any] = {
        "account_equity": float(account_equity),
        "risk_pct": float(risk_pct),
        "credit_usage_pct": float(credit_usage_pct),
        "lot_size": int(lot),
    }

    # ------------------------------
    # 内部：共通計算ルーチン
    # ------------------------------
    def _compute_one(label: str, env: BrokerEnv) -> Dict[str, Any]:
        qty = 0
        required_cash = 0.0
        est_pl = 0.0
        est_loss = 0.0
        reason_msg = ""
        reason_code = ""
        ev_net = None
        rr_net = None
        ev_true = None  # 期待値的な拡張枠（現状は未使用でもOK）

        risk_assets = max(env.cash_yen + env.stock_value, 0.0)
        total_budget = max(env.credit_yoryoku, 0.0)

        # 口座側の credit_usage_pct を掛けた“使用可能予算”
        budget = total_budget * (float(credit_usage_pct) / 100.0)

        if risk_assets <= 0 or budget <= 0:
            reason_msg = "信用余力が 0 円のため。"
            reason_code = "no_budget"
            return dict(
                qty=0,
                required_cash=0.0,
                est_pl=0.0,
                est_loss=0.0,
                ev_net=None,
                rr_net=None,
                ev_true=None,
                reason_code=reason_code,
                reason_msg=reason_msg,
            )

        # 1トレードあたり許容損失（円）
        risk_value = risk_assets * (float(risk_pct) / 100.0)

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
            cost_round = _estimate_trading_cost(entry, test_qty) * 2
            net_profit_test = gross_profit_test - cost_round
            rr_test = _safe_div(gross_profit_test, loss_value_test)

            reason_msg = _build_reason_for_zero(
                label,
                qty=qty,
                gross_profit=gross_profit_test,
                net_profit=net_profit_test,
                rr=rr_test,
                budget=budget,
                min_lot=lot,
                loss_value=loss_per_share,
            )
            reason_code = "filtered"
            return dict(
                qty=0,
                required_cash=0.0,
                est_pl=0.0,
                est_loss=0.0,
                ev_net=None,
                rr_net=None,
                ev_true=None,
                reason_code=reason_code,
                reason_msg=reason_msg,
            )

        # 採用候補としてPL計算（手数料込み）
        gross_profit = reward_per_share * qty
        loss_value = loss_per_share * qty
        cost_round = _estimate_trading_cost(entry, qty) * 2
        net_profit = gross_profit - cost_round
        rr = _safe_div(gross_profit, loss_value)
        rr_net_val = _safe_div(net_profit, loss_value)

        ev_net_val = rr_net_val

        if net_profit <= 0:
            reason_code = "net_profit_negative"
            reason_msg = "手数料・スリッページを考慮すると純利益がマイナスになるため。"
            return dict(
                qty=0,
                required_cash=0.0,
                est_pl=0.0,
                est_loss=0.0,
                ev_net=None,
                rr_net=None,
                ev_true=None,
                reason_code=reason_code,
                reason_msg=reason_msg,
            )

        if net_profit < MIN_NET_PROFIT_YEN:
            reason_code = "profit_too_small"
            reason_msg = f"純利益が {int(MIN_NET_PROFIT_YEN):,} 円未満と小さすぎるため。"
            return dict(
                qty=0,
                required_cash=0.0,
                est_pl=0.0,
                est_loss=0.0,
                ev_net=None,
                rr_net=None,
                ev_true=None,
                reason_code=reason_code,
                reason_msg=reason_msg,
            )

        if rr < MIN_REWARD_RISK:
            reason_code = "rr_too_low"
            reason_msg = f"利確幅に対して損切幅が大きく、R={rr:.2f} と基準未満のため。"
            return dict(
                qty=0,
                required_cash=0.0,
                est_pl=0.0,
                est_loss=0.0,
                ev_net=None,
                rr_net=None,
                ev_true=None,
                reason_code=reason_code,
                reason_msg=reason_msg,
            )

        # 最終採用
        required_cash = entry * qty
        est_pl = net_profit
        est_loss = loss_value
        ev_net = ev_net_val
        rr_net = rr_net_val

        return dict(
            qty=int(qty),
            required_cash=float(required_cash),
            est_pl=float(est_pl),
            est_loss=float(est_loss),
            ev_net=(float(ev_net) if ev_net is not None else None),
            rr_net=(float(rr_net) if rr_net is not None else None),
            ev_true=(float(ev_true) if ev_true is not None else None),
            reason_code="",
            reason_msg="",
        )

    # ------------------------------
    # PRO（C対応：主軸）
    # ------------------------------
    pro_out = _compute_one("PRO", pro_env)
    result["qty_pro"] = int(pro_out["qty"])
    result["required_cash_pro"] = round(float(pro_out["required_cash"] or 0.0), 0)
    result["est_pl_pro"] = round(float(pro_out["est_pl"] or 0.0), 0)
    result["est_loss_pro"] = round(float(pro_out["est_loss"] or 0.0), 0)
    result["ev_net_pro"] = pro_out["ev_net"]
    result["rr_net_pro"] = pro_out["rr_net"]
    result["ev_true_pro"] = pro_out["ev_true"]
    result["reason_pro_code"] = pro_out["reason_code"]
    result["reason_pro_msg"] = pro_out["reason_msg"]

    # ------------------------------
    # 各証券会社（既存）
    # ------------------------------
    for broker_label, short_key in (("楽天", "rakuten"), ("松井", "matsui"), ("SBI", "sbi")):
        env = envs.get(broker_label)

        if env is None:
            result[f"qty_{short_key}"] = 0
            result[f"required_cash_{short_key}"] = 0
            result[f"est_pl_{short_key}"] = 0
            result[f"est_loss_{short_key}"] = 0
            result[f"ev_net_{short_key}"] = None
            result[f"rr_net_{short_key}"] = None
            result[f"ev_true_{short_key}"] = None
            result[f"reason_{short_key}_code"] = "no_account"
            result[f"reason_{short_key}_msg"] = "該当する証券口座の情報が見つからないため。"
            continue

        out = _compute_one(broker_label, env)
        result[f"qty_{short_key}"] = int(out["qty"])
        result[f"required_cash_{short_key}"] = round(float(out["required_cash"] or 0.0), 0)
        result[f"est_pl_{short_key}"] = round(float(out["est_pl"] or 0.0), 0)
        result[f"est_loss_{short_key}"] = round(float(out["est_loss"] or 0.0), 0)
        result[f"ev_net_{short_key}"] = out["ev_net"]
        result[f"rr_net_{short_key}"] = out["rr_net"]
        result[f"ev_true_{short_key}"] = out["ev_true"]
        result[f"reason_{short_key}_code"] = out["reason_code"]
        result[f"reason_{short_key}_msg"] = out["reason_msg"]

    # ------------------------------
    # reasons_text まとめ
    # ------------------------------
    reasons_lines: List[str] = []

    # PRO を先頭に出す（C）
    if result.get("qty_pro", 0) == 0:
        msg = result.get("reason_pro_msg") or ""
        if msg:
            reasons_lines.append(f"・PRO: {msg}")

    for broker_label, short_key in (("楽天", "rakuten"), ("松井", "matsui"), ("SBI", "sbi")):
        msg = result.get(f"reason_{short_key}_msg") or ""
        qtyv = result.get(f"qty_{short_key}", 0)
        if qtyv == 0 and msg:
            reasons_lines.append(f"・{broker_label}: {msg}")

    result["reasons_text"] = reasons_lines or None
    return result