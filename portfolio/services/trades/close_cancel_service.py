# [FILE] close_cancel_service.py
# [PATH] portfolio/services/trades/close_cancel_service.py
#
# このファイルは何？
# - 手仕舞い取消の service
# - 現物売 / 信用返済 を取り消して、保有・実現損益・台帳を元に戻す
#
# 今回の方針
# - 現金台帳の「手仕舞い取消」から呼ばれる
# - TradeEvent を基準に戻す
# - 現物売 / 信用返済のみ対応
# - 現引は今回は触らない

# -*- coding: utf-8 -*-
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from django.core.exceptions import ValidationError
from django.db import transaction

from ...models import Holding, RealizedTrade, TradeEvent
from ...models_cash import CashLedger
from ..cash.ledger_service import delete_trade_event_ledgers


@dataclass
class CloseCancelResult:
    restored_holding: Holding
    deleted_realized_trade: bool
    deleted_trade_event: bool


def _to_dec(v, default: str = "0") -> Decimal:
    try:
        if v in (None, ""):
            return Decimal(default)
        return Decimal(str(v))
    except Exception:
        return Decimal(default)


def _same_holding_filter(event: TradeEvent, restore_side: str) -> dict:
    return {
        "user": event.user,
        "broker": event.broker,
        "account": event.account,
        "ticker": event.ticker,
        "market": (event.country or "JP").upper(),
        "currency": (event.currency or "JPY").upper(),
        "side": restore_side,
    }


def _restore_side_from_event(event: TradeEvent) -> str:
    side = (event.side or "").upper()
    return "BUY" if side == "SELL" else "SELL"


def _merge_or_create_holding(event: TradeEvent) -> Holding:
    restore_side = _restore_side_from_event(event)
    qty_add = int(event.qty or 0)
    if qty_add <= 0:
        raise ValidationError("取消対象の数量が不正です。")

    basis = _to_dec(event.basis or event.price)
    if basis <= 0:
        raise ValidationError("取消対象の取得単価が不正です。")

    base = _same_holding_filter(event, restore_side)
    existing = (
        Holding.objects.filter(**base)
        .order_by("opened_at", "id")
        .first()
    )

    fx_new = _to_dec(event.open_fx_rate or event.fx_rate or event.close_fx_rate or "0")
    cur = (event.currency or "JPY").upper()

    if existing:
        q_old = int(existing.quantity or 0)
        q_total = q_old + qty_add

        p_old = _to_dec(existing.avg_cost or "0")
        total_cost_ccy = (p_old * q_old) + (basis * qty_add)
        if q_total > 0 and total_cost_ccy > 0:
            existing.avg_cost = total_cost_ccy / Decimal(q_total)

        if cur != "JPY":
            fx_old = _to_dec(existing.fx_rate or "0")
            if fx_old > 0 and fx_new > 0 and total_cost_ccy > 0:
                total_cost_jpy = (p_old * fx_old * q_old) + (basis * fx_new * qty_add)
                existing.fx_rate = total_cost_jpy / total_cost_ccy
            elif fx_old <= 0 and fx_new > 0:
                existing.fx_rate = fx_new
        else:
            existing.fx_rate = None

        existing.quantity = q_total

        if event.opened_at and existing.opened_at:
            existing.opened_at = min(existing.opened_at, event.opened_at)
        else:
            existing.opened_at = existing.opened_at or event.opened_at or event.trade_at

        if not existing.name:
            existing.name = event.name or ""
        if not getattr(existing, "sector", ""):
            existing.sector = event.sector33_name or ""

        existing.save()
        return existing

    return Holding.objects.create(
        user=event.user,
        broker=event.broker,
        account=event.account,
        side=restore_side,
        ticker=event.ticker,
        name=event.name or "",
        sector=event.sector33_name or "",
        quantity=qty_add,
        avg_cost=basis,
        market=(event.country or "JP").upper(),
        currency=(event.currency or "JPY").upper(),
        fx_rate=(fx_new if cur != "JPY" and fx_new > 0 else None),
        opened_at=event.opened_at or event.trade_at,
        memo="手仕舞い取消で復元",
    )


def _delete_realized_ledgers(realized_trade_id: int) -> None:
    CashLedger.objects.filter(
        source_id=realized_trade_id,
        source_type__in=["REAL", "REALIZED", "RT"],
    ).delete()


@transaction.atomic
def cancel_close_trade_event(event: TradeEvent) -> CloseCancelResult:
    if event.event_type not in (
        TradeEvent.EventType.SPOT_SELL,
        TradeEvent.EventType.MARGIN_CLOSE,
    ):
        raise ValidationError("このイベントは手仕舞い取消に対応していません。")

    realized = event.realized_trade
    restored_holding = _merge_or_create_holding(event)

    delete_trade_event_ledgers(event.id)

    deleted_realized_trade = False
    if realized is not None:
        _delete_realized_ledgers(realized.id)
        realized.delete()
        deleted_realized_trade = True

    event.delete()

    return CloseCancelResult(
        restored_holding=restored_holding,
        deleted_realized_trade=deleted_realized_trade,
        deleted_trade_event=True,
    )