# -*- coding: utf-8 -*-
"""
AI Picks 用 ポジションサイジングサービス（短期 × 攻め・本気ロジック）

・楽天 / 松井 それぞれについて「数量 / 必要資金 / 想定利益 / 想定損失」を計算
・UserSetting.risk_pct（1トレードあたりのリスク％）を使用
・ATR から損切幅を算出 → 許容損失から数量を決定
・ETF(13xx / 15xx) は 1株単位、それ以外は 100株単位
・手数料 + スリッページをざっくり見積もり、コスト負け・利益ショボい場合は 0株
・証券会社ごとの「安全上限（資産 × レバレッジ上限）」を超える枚数は出さない
・0株になった理由は reasons_text に格納（楽天/松井別々の理由を出す）

※ 資産は DB からではなく、呼び出し側から渡される broker_numbers を利用する
"""

from __future__ import annotations
from typing import Dict, Any, List

from portfolio.models import UserSetting


# ===== パラメータ（あとでポリシー化予定） ===================================

# ETF / ETN のコードプレフィックス
ETF_PREFIXES = ("13", "15")

# 信用も含めた「1銘柄あたりの安全レバレッジ上限」
# 例: 資産 300万なら 300万 × 1.5 = 450万 まで
MAX_LEVERAGE_PER_TRADE = 1.5

# 手数料・スリッページ見積り
FEE_PCT = 0.0005          # 片道 0.05% 想定
FEE_MIN = 110.0           # 片道最低 110 円
SLIPPAGE_PCT = 0.001      # 0.1% をスリッページとして見る（往復分はコストに含める）

# 「利益ショボい」とみなす純利益（コスト控除後）のしきい値
MIN_NET_PROFIT = 3000.0   # 3,000 円未満は見送り


# ===== 内部ヘルパ =========================================================


def _lot_size_for(code: str) -> int:
    """
    ETF/ETN (13xx / 15xx) → 1株
    その他日本株 → 100株
    """
    if code.startswith(ETF_PREFIXES):
        return 1
    return 100


def _risk_pct(user) -> float:
    """
    UserSetting.risk_pct を取得（無ければ 1%）
    """
    try:
        s = UserSetting.objects.get(user=user)
        return float(s.risk_pct or 1.0)
    except UserSetting.DoesNotExist:
        return 1.0


def _estimate_roundtrip_cost(notional: float) -> float:
    """
    1 回の建て玉（片サイド）の想定約定代金 notional に対して、
    ・売買手数料（往復）
    ・スリッページ
    をざっくり見積もる。
    """
    if notional <= 0:
        return 0.0

    # 手数料（往復分）
    fee_one_way = max(FEE_MIN, notional * FEE_PCT)
    fee_round = fee_one_way * 2.0

    # スリッページ（約定代金に対して 0.1% 分をコストとみなす）
    slip = notional * SLIPPAGE_PCT

    return fee_round + slip


def _get_assets_from_numbers(broker_label: str, broker_numbers: Any | None) -> float:
    """
    AI設定の「証券サマリ」で使っている broker_numbers から、
    その証券会社の“リスクベース資産”を推定する。

    BrokerNumbers / dict 双方に対応できるよう、代表的なフィールド名を総当たりで見る。
    見つからなければ 0 を返す（その場合は「資産ゼロ」で 0株判定になる）。
    """
    if not broker_numbers:
        return 0.0

    def _get(obj, name: str):
        if isinstance(obj, dict):
            return obj.get(name)
        if hasattr(obj, name):
            return getattr(obj, name)
        return None

    for b in broker_numbers:
        label = _get(b, "label") or _get(b, "broker_label")
        if label != broker_label:
            continue

        # 1. まず「リスク用に計算済みの合計」があればそれを優先
        for key in [
            "risk_assets", "risk_asset", "risk_base",
            "asset_total", "assets", "total_assets",
        ]:
            v = _get(b, key)
            if v is not None:
                try:
                    return float(v or 0)
                except (TypeError, ValueError):
                    pass

        # 2. 現金 + 現物評価額 からざっくり合計を作る
        cash = 0.0
        spot = 0.0

        for key in ["cash_balance", "cash_remain", "cash", "cash_total"]:
            v = _get(b, key)
            if v is not None:
                try:
                    cash = float(v or 0)
                except (TypeError, ValueError):
                    pass
                break

        for key in ["spot_value", "spot_eval", "spot_equity", "equity", "stock_value"]:
            v = _get(b, key)
            if v is not None:
                try:
                    spot = float(v or 0)
                except (TypeError, ValueError):
                    pass
                break

        if cash or spot:
            return cash + spot

        # 3. 何も取れなければ 0
        return 0.0

    return 0.0


# ===== メインロジック =====================================================


def compute_position_sizing(
    user,
    code: str,
    last_price: float,
    atr: float,
    entry: float,
    tp: float,
    sl: float,
    broker_numbers: Any | None = None,
) -> Dict[str, Any]:
    """
    AI Picks 1銘柄分の数量を楽天・松井の2段で返す。

    戻り値のキー:
        qty_rakuten, qty_matsui
        required_cash_rakuten, required_cash_matsui
        est_pl_rakuten, est_pl_matsui
        est_loss_rakuten, est_loss_matsui
        risk_pct, lot_size, reasons_text
    """
    lot = _lot_size_for(code)
    risk_pct = _risk_pct(user)

    # ATR や価格が変な場合は即見送り
    if not atr or atr <= 0 or last_price <= 0 or entry <= 0 or tp <= 0 or sl <= 0:
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
            reasons_text=[
                "この銘柄は短期ルール上「見送り」です。",
                "・ATR または価格情報が不足/異常のため。",
            ],
        )

    out: Dict[str, Any] = {}
    reasons: Dict[str, str] = {}

    # 共有の値
    # エントリーと SL の差をベースに、最低でも ATR×0.6 は損切幅として見る
    loss_per_share = max(entry - sl, atr * 0.6)
    profit_per_share = max(tp - entry, atr * 0.4)

    # 最小ロットでも損切幅がゼロ or マイナスなら安全のため見送り
    if loss_per_share <= 0:
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
            reasons_text=[
                "この銘柄は短期ルール上「見送り」です。",
                "・損切幅がほとんど無く、リスクリワードが成立しないため。",
            ],
        )

    # 楽天 / 松井 の2つを同じロジックで回す
    for broker_label, broker_name in [("rakuten", "楽天"), ("matsui", "松井")]:
        # その証券会社のざっくり資産（AI証券サマリと同じベース）
        assets = _get_assets_from_numbers(broker_label, broker_numbers)

        qty = 0
        est_pl = 0.0
        est_loss = 0.0
        reason: str | None = None

        if assets <= 0:
            reason = "口座残高・保有株が無く、トレード資産がゼロのため。"
        else:
            # 1トレードあたりの許容損失（円）
            risk_value = assets * (risk_pct / 100.0)

            # 許容損失ベースの枚数
            qty_risk = int((risk_value / loss_per_share) // lot * lot)

            if qty_risk < lot:
                # 許容リスクが小さすぎて最小ロットでも建てられない
                reason = "許容損失に対して最小ロットが大きすぎるため。"
            else:
                # 安全上限（資産 × レバレッジ上限）から見た最大枚数
                max_notional = assets * MAX_LEVERAGE_PER_TRADE
                max_qty_by_budget = int((max_notional / entry) // lot * lot)

                if max_qty_by_budget < lot:
                    reason = "安全上限（資産×レバレッジ上限）内では最小ロットも建てられないため。"
                else:
                    # リスクと安全上限の両方を満たす範囲での枚数
                    qty_candidate = min(qty_risk, max_qty_by_budget)

                    if qty_candidate < lot:
                        # 理論上ほぼ起きないが、保険として
                        reason = "リスク制約と安全上限の両方を考慮すると、建てられるロットが無いため。"
                    else:
                        notional = entry * qty_candidate
                        cost = _estimate_roundtrip_cost(notional)

                        gross_pl = profit_per_share * qty_candidate
                        loss_amt = loss_per_share * qty_candidate

                        net_pl = gross_pl - cost      # TP到達時に本当に残る利益
                        net_loss = loss_amt + cost    # SL+コストでどれだけ失うか

                        # コスト負け / 利益ショボい判定
                        if net_pl <= 0:
                            reason = "TP到達時でも手数料・スリッページを差し引くと利益が残らないため。"
                        elif net_pl < MIN_NET_PROFIT:
                            reason = "TP到達時の純利益が小さく、手間に見合うリターンにならないため。"
                        else:
                            # OK: この枚数で採用
                            qty = qty_candidate
                            est_pl = net_pl
                            est_loss = net_loss

        # 結果を out に詰める
        out[f"qty_{broker_label}"] = qty
        out[f"required_cash_{broker_label}"] = int(round(entry * qty, 0)) if qty > 0 else 0
        out[f"est_pl_{broker_label}"] = int(round(est_pl, 0)) if qty > 0 else 0
        out[f"est_loss_{broker_label}"] = int(round(est_loss, 0)) if qty > 0 else 0

        if reason:
            reasons[broker_label] = reason

    # ===== 理由テキストまとめ（楽天/松井別々の理由を出す） ===================

    reasons_lines: List[str] = []
    if reasons:
        reasons_lines.append("この銘柄は短期ルール上「見送り」または一部見送りです。")
        if "rakuten" in reasons:
            reasons_lines.append(f"・楽天: {reasons['rakuten']}")
        if "matsui" in reasons:
            reasons_lines.append(f"・松井: {reasons['matsui']}")

    out["risk_pct"] = risk_pct
    out["lot_size"] = lot
    out["reasons_text"] = reasons_lines or None

    return out