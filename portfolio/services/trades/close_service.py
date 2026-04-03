# [FILE] close_service.py
# [PATH] portfolio/services/trades/close_service.py
#
# このファイルは何？
# - 保有クローズ（現物売り / 信用返済）を処理する service
# - Holding 減算
# - TradeEvent 作成
# - RealizedTrade 作成
# - CashLedger 反映
#
# 今回の修正ポイント
# - 「実損入力を主役にして手数料を逆算する」仕様に戻す
# - ただし、実損が未入力のときは逆算しない
# - 未入力時は fee=0 として通常登録できるようにする
# - 負の fee を保存しようとして登録不能になる不具合を防ぐ

# -*- coding: utf-8 -*-
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F

from ...models import Holding, RealizedTrade, TradeEvent
from ..cash.ledger_service import upsert_trade_event_ledger


@dataclass
class CloseResult:
    realized_trade: RealizedTrade
    trade_event: TradeEvent
    holding_deleted: bool


def _to_dec(v, default="0") -> Decimal:
    try:
        return Decimal(str(v if v not in (None, "") else default))
    except Exception:
        return Decimal(default)


def _holding_basis(h: Holding) -> Decimal:
    for fname in ["avg_cost", "basis"]:
        v = getattr(h, fname, None)
        if v not in (None, ""):
            return Decimal(str(v))
    return Decimal("0")


def _opposite_side(side: str) -> str:
    s = (side or "").upper()
    return "SELL" if s == "BUY" else "BUY"


@transaction.atomic
def close_holding(
    *,
    holding: Holding,
    trade_at,
    side_in: str,
    qty_in: int,
    price,
    tax_in,
    pnl_input,
    pnl_input_given: bool,
    broker: str,
    account: str,
    memo: str,
    name: str,
    sector33_code: str,
    sector33_name: str,
    country: str,
    currency: str,
    open_fx_rate,
    close_fx_rate,
    strategy_label: str,
    policy_key: str,
    is_ai_signal: bool,
    position_key: str,
) -> CloseResult:
    held_qty = int(holding.quantity or 0)
    if qty_in <= 0 or qty_in > held_qty:
        raise ValidationError("数量が不正です。")

    holding_side = (holding.side or "BUY").upper()
    if side_in != _opposite_side(holding_side):
        raise ValidationError("クローズは反対売買のみです。")

    basis = _holding_basis(holding)
    price = _to_dec(price)
    tax_in = _to_dec(tax_in)
    pnl_input = _to_dec(pnl_input)

    if basis <= 0:
        raise ValidationError("保有の平均取得単価が不正です。")

    # =========================================================
    # 元の使い方に戻す
    # - 実損入力あり: その値を主役にして fee を逆算
    # - 実損入力なし: fee は 0 として通常登録
    # =========================================================
    if pnl_input_given:
        if holding_side == "BUY" and side_in == "SELL":
            fee = ((price - basis) * Decimal(qty_in)) - pnl_input - tax_in
        else:
            fee = ((basis - price) * Decimal(qty_in)) - pnl_input - tax_in

        if fee < 0:
            raise ValidationError("実損入力が価格条件と合いません。実損・単価・税金を確認してください。")
    else:
        fee = Decimal("0")

    opened_date = holding.opened_at
    days_held = None
    if opened_date:
        days_held = max((trade_at - opened_date).days, 0)

    rt = RealizedTrade.objects.create(
        user=holding.user,
        trade_at=trade_at,
        opened_at=opened_date,
        side=side_in,
        ticker=holding.ticker,
        name=name or holding.name or "",
        sector33_code=sector33_code or "",
        sector33_name=sector33_name or holding.sector or "",
        qty=qty_in,
        price=price,
        basis=basis,
        fee=fee,
        tax=tax_in,
        broker=broker,
        account=account,
        country=country or holding.market or "JP",
        currency=currency or holding.currency or "JPY",
        open_fx_rate=open_fx_rate,
        close_fx_rate=close_fx_rate,
        fx_rate=close_fx_rate or open_fx_rate,
        cashflow=pnl_input,
        hold_days=days_held,
        strategy_label=strategy_label or "",
        policy_key=policy_key or "",
        is_ai_signal=bool(is_ai_signal),
        position_key=position_key or "",
        memo=memo or "",
    )

    if (holding.account or "").upper() == "MARGIN":
        event_type = TradeEvent.EventType.MARGIN_CLOSE
        cash_amount_jpy = int(round(float(rt.pnl_jpy or 0)))
    else:
        event_type = (
            TradeEvent.EventType.SPOT_SELL
            if side_in == "SELL"
            else TradeEvent.EventType.SPOT_BUY
        )
        cash_amount_jpy = None

    ev = TradeEvent.objects.create(
        user=holding.user,
        holding=holding,
        realized_trade=rt,
        trade_at=trade_at,
        opened_at=opened_date,
        event_type=event_type,
        side=side_in,
        ticker=holding.ticker,
        name=name or holding.name or "",
        sector33_code=sector33_code or "",
        sector33_name=sector33_name or holding.sector or "",
        qty=qty_in,
        price=price,
        basis=basis,
        fee=fee,
        tax=tax_in,
        broker=broker,
        account=account,
        country=country or holding.market or "JP",
        currency=currency or holding.currency or "JPY",
        open_fx_rate=open_fx_rate,
        close_fx_rate=close_fx_rate,
        fx_rate=close_fx_rate or open_fx_rate,
        cash_amount_jpy=cash_amount_jpy,
        memo=memo or "",
        position_key=position_key or "",
    )

    upsert_trade_event_ledger(ev)

    holding.quantity = F("quantity") - qty_in
    holding.save(update_fields=["quantity"])
    holding.refresh_from_db()

    holding_deleted = False
    if int(holding.quantity or 0) <= 0:
        holding.delete()
        holding_deleted = True

    return CloseResult(
        realized_trade=rt,
        trade_event=ev,
        holding_deleted=holding_deleted,
    )