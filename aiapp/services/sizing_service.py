# -*- coding: utf-8 -*-
"""
AI Picks 数量・必要資金・損益を証券会社別に算出するサービス（短期×攻め 用ベース）

- 楽天 / 松井 の 2 段出力（qty, required_cash, est_pl, est_loss）
- リスク％は portfolio.models.UserSetting.risk_pct を使用
- 「証券サマリ」と同じロジックで資産・信用余力を取得する
    - aiapp.services.broker_summary.compute_broker_summaries を共通利用
    - 現金残高 + 現物評価額 = ブローカー別の「リスクベース資産」
    - 信用余力（概算） = そのブローカーで新規に使える上限
- 1トレードあたりの許容損失 = リスクベース資産 × (risk_pct / 100)
  ただし実際に使える金額（信用余力）も上限として効かせる
- 損切幅 = ATR × 0.6 （短期×攻めロジックのベース）
- ETF/ETN (13xx / 15xx) は 1株、それ以外は 100株単位
"""

from __future__ import annotations

from typing import Any, Dict, Tuple

from portfolio.models import UserSetting
from aiapp.services.broker_summary import compute_broker_summaries


# =========================
# ヘルパ
# =========================

def _lot_size_for(code: str) -> int:
    """
    ETF/ETN (13xx / 15xx) → 1株
    それ以外（日本株） → 100株
    """
    if code.startswith("13") or code.startswith("15"):
        return 1
    return 100


def _risk_pct(user) -> float:
    """
    UserSetting.risk_pct を取得（無い/不正なら 1.0）
    """
    try:
        s = UserSetting.objects.get(user=user)
        return float(s.risk_pct or 1.0)
    except UserSetting.DoesNotExist:
        return 1.0
    except Exception:
        return 1.0


def _load_leverage_params(user) -> Tuple[float, float, float, float]:
    """
    UserSetting から 楽天/松井 の 倍率・ヘアカット を取得。
    存在しない場合はデフォルト値で補完。
    """
    # デフォルト（今使っている想定値）
    rakuten_lev_default = 2.90
    rakuten_hc_default = 0.30
    matsui_lev_default = 2.80
    matsui_hc_default = 0.00

    try:
        us = UserSetting.objects.get(user=user)
    except UserSetting.DoesNotExist:
        return (
            rakuten_lev_default,
            rakuten_hc_default,
            matsui_lev_default,
            matsui_hc_default,
        )

    def _get(obj, name, default):
        v = getattr(obj, name, None)
        try:
            return float(v)
        except Exception:
            return default

    rakuten_leverage = _get(us, "leverage_rakuten", rakuten_lev_default)
    rakuten_haircut = _get(us, "haircut_rakuten", rakuten_hc_default)
    matsui_leverage = _get(us, "leverage_matsui", matsui_lev_default)
    matsui_haircut = _get(us, "haircut_matsui", matsui_hc_default)

    return rakuten_leverage, rakuten_haircut, matsui_leverage, matsui_haircut


def _get_risk_base_and_budget(user, broker_label: str) -> Tuple[float, float]:
    """
    証券サマリと同じロジックで
        ・リスクベース資産（現金＋現物評価額）
        ・新規トレードに使える上限（信用余力）
    を取得する。

    戻り値: (risk_assets, available_budget)
        risk_assets:   リスク％を掛けるベースとなる資産額
        available_budget: 入るとしてもこの金額以内に抑える上限（信用余力）
    """
    risk_pct = _risk_pct(user)
    rakuten_leverage, rakuten_haircut, matsui_leverage, matsui_haircut = _load_leverage_params(user)

    brokers = compute_broker_summaries(
        user=user,
        risk_pct=risk_pct,
        rakuten_leverage=rakuten_leverage,
        rakuten_haircut=rakuten_haircut,
        matsui_leverage=matsui_leverage,
        matsui_haircut=matsui_haircut,
    )

    # compute_broker_summaries は BrokerNumbers オブジェクトのリストを返す想定
    for b in brokers:
        label = getattr(b, "label", None)
        if label != broker_label:
            continue

        # リスクベース資産 = 現金残高 + 現物評価額
        cash = float(getattr(b, "cash_yen", 0.0) or 0.0)

        # broker_summary 側のフィールド名に両対応
        stock_eval = getattr(b, "stock_eval", None)
        stock_acq = getattr(b, "stock_acq_value", None)
        stock = float(stock_eval if stock_eval is not None else stock_acq or 0.0)

        risk_assets = max(0.0, cash + stock)

        # 新規トレードに使える上限（金額としての信用余力）
        credit_yoryoku = float(getattr(b, "credit_yoryoku", 0.0) or 0.0)
        available_budget = max(0.0, credit_yoryoku)

        return risk_assets, available_budget

    # 見つからなければ 0 扱い
    return 0.0, 0.0


# =========================
# メイン計算
# =========================

def compute_position_sizing(
    user,
    code: str,
    last_price: float,
    atr: float,
    *,
    entry: float | None = None,
    tp: float | None = None,
    sl: float | None = None,
) -> Dict[str, Any]:
    """
    AI Picks 1銘柄分の数量を 楽天・松井 の 2段で返す。

    ※ entry / tp / sl は今は使っていないが、
       picks_build からキーワード引数で渡されるので受け取って無視する。

    戻り値のキー:
        qty_rakuten, qty_matsui
        required_cash_rakuten, required_cash_matsui
        est_pl_rakuten, est_pl_matsui
        est_loss_rakuten, est_loss_matsui
        risk_pct, lot_size
    """
    lot = _lot_size_for(code)
    risk_pct = _risk_pct(user)

    # ATR が 0、株価が 0 以下ならトレード不可
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

    out: Dict[str, Any] = {}

    for broker_label, key_prefix in [("楽天", "rakuten"), ("松井", "matsui")]:
        risk_assets, available_budget = _get_risk_base_and_budget(user, broker_label)

        # 資産 or 余力が 0 以下なら 0株
        if risk_assets <= 0 or available_budget <= 0:
            qty = 0
            required_cash = 0.0
            est_pl = 0.0
            est_loss = 0.0
        else:
            # 1トレードあたりの許容損失
            risk_value = risk_assets * (risk_pct / 100.0)

            # 実際に使える金額（信用余力）を超えないように、ここでも上限をかける
            effective_risk_value = min(risk_value, available_budget)

            # 損切幅：ATR の 0.6倍（短期×攻め）
            loss_per_share = atr * 0.6

            # ロット単位で丸める
            qty = int((effective_risk_value / loss_per_share) // lot * lot)

            if qty <= 0:
                qty = 0
                required_cash = 0.0
                est_pl = 0.0
                est_loss = 0.0
            else:
                required_cash = qty * float(last_price)

                # 利確/損切の概算（エントリー→TP/SL の値幅）
                est_pl = atr * 0.8 * qty          # 想定利益
                est_loss = loss_per_share * qty   # 想定損失

        out[f"qty_{key_prefix}"] = qty
        out[f"required_cash_{key_prefix}"] = round(required_cash, 0)
        out[f"est_pl_{key_prefix}"] = round(est_pl, 0)
        out[f"est_loss_{key_prefix}"] = round(est_loss, 0)

    out["risk_pct"] = risk_pct
    out["lot_size"] = lot
    return out