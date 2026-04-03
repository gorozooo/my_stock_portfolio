# [FILE] margin_to_spot_cancel_service.py
# [PATH] portfolio/services/trades/margin_to_spot_cancel_service.py
#
# このファイルは何？
# - 現引の取消専用 service
# - 現引 TradeEvent を元に、現物側を戻して信用側を復元する
#
# 今回の方針
# - 現引は編集しない
# - 間違えたときは「取消」で戻す
# - CashLedger だけでなく Holding の数量も整合させる

# -*- coding: utf-8 -*-
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from django.core.exceptions import ValidationError
from django.db import transaction

from ...models import Holding, TradeEvent
from ..cash.ledger_service import delete_trade_event_ledgers


@dataclass
class MarginToSpotCancelResult:
    event: TradeEvent
    restored_margin: Holding
    adjusted_spot: Optional[Holding]
    spot_deleted: bool


def _to_dec(v, default: str = "0") -> Decimal:
    try:
        if v in (None, ""):
            return Decimal(default)
        return Decimal(str(v))
    except Exception:
        return Decimal(default)


def _same_security_filter(event: TradeEvent) -> dict:
    return {
        "user": event.user,
        "broker": event.broker,
        "ticker": event.ticker,
        "market": (event.country or "JP").upper(),
        "currency": (event.currency or "JPY").upper(),
        "side": "BUY",
    }


def _pick_spot_holding(event: TradeEvent) -> Holding:
    base = _same_security_filter(event)

    candidates: list[str] = []
    if (event.account or "").upper() in ("SPEC", "NISA"):
        candidates.append((event.account or "").upper())
    candidates.extend(["SPEC", "NISA"])

    seen = set()
    for account in candidates:
        if account in seen:
            continue
        seen.add(account)

        row = (
            Holding.objects.filter(**base, account=account)
            .order_by("opened_at", "id")
            .first()
        )
        if row:
            return row

    raise ValidationError("取消対象の現物保有が見つかりません。")


def _restore_margin_holding(event: TradeEvent) -> Holding:
    base = _same_security_filter(event)
    qty_add = int(event.qty or 0)
    if qty_add <= 0:
        raise ValidationError("現引数量が不正です。")

    basis = _to_dec(event.basis or event.price)
    if basis <= 0:
        raise ValidationError("現引イベントの取得単価が不正です。")

    fx_new = _to_dec(event.open_fx_rate or event.fx_rate or event.close_fx_rate or "0")
    cur = (event.currency or "JPY").upper()

    margin = (
        Holding.objects.filter(**base, account="MARGIN")
        .order_by("opened_at", "id")
        .first()
    )

    if margin:
        q_old = int(margin.quantity or 0)
        q_total = q_old + qty_add

        p_old = _to_dec(margin.avg_cost or "0")
        total_cost_ccy = (p_old * q_old) + (basis * qty_add)
        if q_total > 0 and total_cost_ccy > 0:
            margin.avg_cost = total_cost_ccy / Decimal(q_total)

        if cur != "JPY":
            fx_old = _to_dec(margin.fx_rate or "0")
            if fx_old > 0 and fx_new > 0 and total_cost_ccy > 0:
                total_cost_jpy = (p_old * fx_old * q_old) + (basis * fx_new * qty_add)
                margin.fx_rate = total_cost_jpy / total_cost_ccy
            elif fx_new > 0 and fx_old <= 0:
                margin.fx_rate = fx_new
        else:
            margin.fx_rate = None

        margin.quantity = q_total
        if event.opened_at and margin.opened_at:
            margin.opened_at = min(margin.opened_at, event.opened_at)
        else:
            margin.opened_at = margin.opened_at or event.opened_at or event.trade_at

        if not margin.name:
            margin.name = event.name or ""
        if not getattr(margin, "sector", ""):
            margin.sector = event.sector33_name or ""
        if event.memo:
            if margin.memo:
                margin.memo = f"{margin.memo}\n現引取消で復元"
            else:
                margin.memo = "現引取消で復元"

        margin.save()
        return margin

    return Holding.objects.create(
        user=event.user,
        broker=event.broker,
        account="MARGIN",
        side="BUY",
        ticker=event.ticker,
        name=event.name or "",
        sector=event.sector33_name or "",
        quantity=qty_add,
        avg_cost=basis,
        market=(event.country or "JP").upper(),
        currency=(event.currency or "JPY").upper(),
        fx_rate=(fx_new if cur != "JPY" and fx_new > 0 else None),
        opened_at=event.opened_at or event.trade_at,
        memo="現引取消で復元",
    )


def _rollback_spot_holding(event: TradeEvent, spot: Holding) -> tuple[Optional[Holding], bool]:
    qty_sub = int(event.qty or 0)
    qty_now = int(spot.quantity or 0)
    if qty_sub <= 0 or qty_sub > qty_now:
        raise ValidationError("現物側の数量が不足しているため取消できません。")

    if qty_now == qty_sub:
        spot.delete()
        return None, True

    remain_qty = qty_now - qty_sub
    cur = (spot.currency or "JPY").upper()

    current_avg = _to_dec(spot.avg_cost or "0")
    remove_avg = _to_dec(event.basis or event.price or "0")

    total_cost_ccy = current_avg * qty_now
    remove_cost_ccy = remove_avg * qty_sub
    remain_cost_ccy = total_cost_ccy - remove_cost_ccy

    if remain_qty > 0 and remain_cost_ccy > 0:
        spot.avg_cost = remain_cost_ccy / Decimal(remain_qty)

    if cur != "JPY":
        current_fx = _to_dec(spot.fx_rate or "0")
        remove_fx = _to_dec(event.open_fx_rate or event.fx_rate or event.close_fx_rate or "0")
        total_cost_jpy = current_avg * current_fx * qty_now
        remove_cost_jpy = remove_avg * remove_fx * qty_sub
        remain_cost_jpy = total_cost_jpy - remove_cost_jpy

        if remain_cost_ccy > 0 and remain_cost_jpy > 0:
            spot.fx_rate = remain_cost_jpy / remain_cost_ccy

    spot.quantity = remain_qty
    spot.save()
    return spot, False


@transaction.atomic
def cancel_margin_to_spot(event: TradeEvent) -> MarginToSpotCancelResult:
    if event.event_type != TradeEvent.EventType.MARGIN_TO_SPOT:
        raise ValidationError("現引イベントではありません。")

    spot = _pick_spot_holding(event)
    adjusted_spot, spot_deleted = _rollback_spot_holding(event, spot)
    restored_margin = _restore_margin_holding(event)

    delete_trade_event_ledgers(event.id)
    event.delete()

    return MarginToSpotCancelResult(
        event=event,
        restored_margin=restored_margin,
        adjusted_spot=adjusted_spot,
        spot_deleted=spot_deleted,
    )