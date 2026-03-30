# [FILE] margin_to_spot_service.py
# [PATH] portfolio/services/trades/margin_to_spot_service.py
#
# このファイルは何？
# - 信用保有を現物へ移す「現引」専用 service
# - source: MARGIN の Holding
# - target: SPEC の Holding
# - TradeEvent(MARGIN_TO_SPOT) を作成
# - CashLedger へ現引額を反映
#
# 今回の方針
# - 現引は「編集」ではなく正式イベントとして扱う
# - 実現損益は作らない
# - 現引先は現実運用に合わせて SPEC（特定）固定にする
# - 信用売り（SELL）は今回は対象外。将来「現渡」で別実装する

# -*- coding: utf-8 -*-
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from django.core.exceptions import ValidationError
from django.db import transaction

from ...models import Holding, TradeEvent
from ..cash.ledger_service import upsert_trade_event_ledger


@dataclass
class MarginToSpotResult:
    source_holding_deleted: bool
    source_holding: Optional[Holding]
    target_holding: Holding
    trade_event: TradeEvent


def _to_dec(v, default="0") -> Decimal:
    try:
        return Decimal(str(v if v not in (None, "") else default))
    except Exception:
        return Decimal(default)


def _weighted_avg(old_qty: int, old_cost: Decimal, add_qty: int, add_cost: Decimal) -> Decimal:
    total_qty = old_qty + add_qty
    if total_qty <= 0:
        return Decimal("0")
    total_cost = (old_cost * old_qty) + (add_cost * add_qty)
    return total_cost / Decimal(total_qty)


def _weighted_fx(
    old_qty: int,
    old_cost: Decimal,
    old_fx: Optional[Decimal],
    add_qty: int,
    add_cost: Decimal,
    add_fx: Optional[Decimal],
) -> Optional[Decimal]:
    if old_fx is None and add_fx is None:
        return None
    if old_fx is None:
        return add_fx
    if add_fx is None:
        return old_fx

    total_ccy = (old_cost * old_qty) + (add_cost * add_qty)
    if total_ccy <= 0:
        return add_fx

    total_jpy = (old_cost * old_fx * old_qty) + (add_cost * add_fx * add_qty)
    return total_jpy / total_ccy


def _get_or_create_target_spot_holding(src: Holding, move_qty: int) -> Holding:
    """
    現引先は SPEC 固定。
    同じ銘柄の特定口座現物があれば平均取得で統合する。
    """
    unit_cost = _to_dec(src.avg_cost)
    fx_rate = _to_dec(src.fx_rate) if src.fx_rate not in (None, "") else None

    target = (
        Holding.objects.filter(
            user=src.user,
            broker=src.broker,
            account="SPEC",
            ticker=src.ticker,
            market=src.market,
            currency=src.currency,
            side="BUY",
        )
        .order_by("opened_at", "id")
        .first()
    )

    if target:
        old_qty = int(target.quantity or 0)
        old_cost = _to_dec(target.avg_cost)

        target.avg_cost = _weighted_avg(old_qty, old_cost, move_qty, unit_cost)
        target.quantity = old_qty + move_qty

        old_fx = _to_dec(target.fx_rate) if target.fx_rate not in (None, "") else None
        target.fx_rate = _weighted_fx(old_qty, old_cost, old_fx, move_qty, unit_cost, fx_rate)

        if src.opened_at and target.opened_at:
            target.opened_at = min(src.opened_at, target.opened_at)
        else:
            target.opened_at = target.opened_at or src.opened_at

        if src.name and not target.name:
            target.name = src.name
        if src.sector and not target.sector:
            target.sector = src.sector

        target.save()
        return target

    return Holding.objects.create(
        user=src.user,
        ticker=src.ticker,
        name=src.name,
        sector=src.sector,
        market=src.market,
        currency=src.currency,
        fx_rate=fx_rate,
        quantity=move_qty,
        avg_cost=unit_cost,
        broker=src.broker,
        side="BUY",
        account="SPEC",
        opened_at=src.opened_at,
        memo="現引で作成",
    )


@transaction.atomic
def execute_margin_to_spot(
    *,
    source_holding: Holding,
    trade_at,
    qty: int,
    memo: str = "",
) -> MarginToSpotResult:
    """
    信用保有 → 現物（特定）へ現引。
    """
    if source_holding.account != "MARGIN":
        raise ValidationError("信用保有のみ現引できます。")

    if (source_holding.side or "").upper() != "BUY":
        raise ValidationError("今回は信用買い建玉の現引のみ対応です。信用売りは将来『現渡』で分けて実装します。")

    held_qty = int(source_holding.quantity or 0)
    if qty <= 0:
        raise ValidationError("現引数量は1以上で入力してください。")
    if qty > held_qty:
        raise ValidationError("現引数量が保有数量を超えています。")

    unit_cost = _to_dec(source_holding.avg_cost)
    if unit_cost <= 0:
        raise ValidationError("平均取得単価が不正です。")

    target_holding = _get_or_create_target_spot_holding(source_holding, qty)

    trade_event = TradeEvent.objects.create(
        user=source_holding.user,
        holding=target_holding,
        trade_at=trade_at,
        opened_at=source_holding.opened_at,
        event_type=TradeEvent.EventType.MARGIN_TO_SPOT,
        side="BUY",
        ticker=source_holding.ticker,
        name=source_holding.name or "",
        sector33_code="",
        sector33_name=source_holding.sector or "",
        qty=qty,
        price=unit_cost,
        basis=unit_cost,
        fee=Decimal("0"),
        tax=Decimal("0"),
        broker=source_holding.broker,
        account="SPEC",
        country=source_holding.market or "JP",
        currency=source_holding.currency or "JPY",
        fx_rate=source_holding.fx_rate,
        open_fx_rate=source_holding.fx_rate,
        close_fx_rate=None,
        memo=(memo or f"現引 {source_holding.ticker}"),
        position_key=f"{source_holding.ticker}-{(source_holding.opened_at or trade_at).isoformat()}-SPEC",
    )

    upsert_trade_event_ledger(trade_event)

    remain = held_qty - qty
    source_holding_deleted = False

    if remain <= 0:
        source_holding.delete()
        source_holding_deleted = True
        source_ref = None
    else:
        source_holding.quantity = remain
        source_holding.save(update_fields=["quantity"])
        source_ref = source_holding

    return MarginToSpotResult(
        source_holding_deleted=source_holding_deleted,
        source_holding=source_ref,
        target_holding=target_holding,
        trade_event=trade_event,
    )