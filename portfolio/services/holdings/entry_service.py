# [FILE] entry_service.py
# [PATH] portfolio/services/holdings/entry_service.py
#
# このファイルは何？
# - 保有登録（現物買い/信用新規）を処理する service
# - Holding 更新
# - TradeEvent 作成
# - CashLedger 反映
#
# 今回の方針
# - Spot 買いは現金出金を必ず残す
# - Margin 新規は現金を動かさない
# - Holding の編集で account 変更はさせず、イベントで扱う

# -*- coding: utf-8 -*-
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from ...models import Holding, TradeEvent
from ..cash.ledger_service import upsert_trade_event_ledger


@dataclass
class HoldingEntryResult:
    holding: Holding
    trade_event: TradeEvent
    merged: bool


def _infer_market_and_currency(ticker: str) -> tuple[str, str]:
    head = (ticker or "").upper().strip()
    if head.isalpha():
        return "US", "USD"
    return "JP", "JPY"


def _merge_existing_holding(new_obj: Holding) -> Optional[Holding]:
    key = dict(
        user=new_obj.user,
        broker=new_obj.broker,
        account=new_obj.account,
        ticker=new_obj.ticker,
        market=new_obj.market,
        currency=new_obj.currency,
        side=new_obj.side,
    )

    existing = (
        Holding.objects.filter(**key)
        .order_by("opened_at", "id")
        .first()
    )
    if not existing:
        return None

    q_old = int(existing.quantity or 0)
    q_new = int(new_obj.quantity or 0)
    if q_new <= 0:
        return existing

    q_total = q_old + q_new
    if q_total <= 0:
        return existing

    p_old = Decimal(existing.avg_cost or 0)
    p_new = Decimal(new_obj.avg_cost or 0)
    total_cost_ccy = (p_old * q_old) + (p_new * q_new)
    existing.avg_cost = total_cost_ccy / Decimal(q_total)

    cur = (existing.currency or new_obj.currency or "JPY").upper()
    if cur != "JPY":
        fx_old = Decimal(existing.fx_rate or 0)
        fx_new = Decimal(new_obj.fx_rate or 0)
        if fx_old > 0 and fx_new > 0 and total_cost_ccy > 0:
            total_cost_jpy = (p_old * fx_old * q_old) + (p_new * fx_new * q_new)
            existing.fx_rate = total_cost_jpy / total_cost_ccy

    existing.quantity = q_total

    op_old = existing.opened_at
    op_new = new_obj.opened_at
    if op_old and op_new:
        existing.opened_at = min(op_old, op_new)
    else:
        existing.opened_at = op_old or op_new

    if new_obj.memo:
        if existing.memo:
            existing.memo = f"{existing.memo}\n{new_obj.memo}"
        else:
            existing.memo = new_obj.memo

    existing.name = new_obj.name or existing.name
    existing.sector = new_obj.sector or existing.sector
    existing.save()
    return existing


def _build_event_type(holding: Holding) -> str:
    account = (holding.account or "").upper()
    if account == "MARGIN":
        return TradeEvent.EventType.MARGIN_OPEN
    return TradeEvent.EventType.SPOT_BUY


@transaction.atomic
def register_holding_entry(new_obj: Holding) -> HoldingEntryResult:
    if not new_obj.user_id:
        raise ValidationError("user が未設定です。")

    if not new_obj.ticker:
        raise ValidationError("ticker が未入力です。")

    if int(new_obj.quantity or 0) <= 0:
        raise ValidationError("数量は1以上で入力してください。")

    if Decimal(new_obj.avg_cost or 0) <= 0:
        raise ValidationError("取得単価は0より大きく入力してください。")

    market, currency = _infer_market_and_currency(new_obj.ticker)
    new_obj.market = market
    new_obj.currency = currency

    if (new_obj.account or "").upper() in ("SPEC", "NISA") and (new_obj.side or "").upper() != "BUY":
        raise ValidationError("現物/NISA の新規登録は BUY のみ対応です。")

    if currency != "JPY" and not new_obj.fx_rate:
        raise ValidationError("外貨建ての買付は fx_rate を入力してください。")

    merged = _merge_existing_holding(new_obj)
    holding = merged if merged else new_obj

    if not merged:
        if not holding.opened_at:
            holding.opened_at = timezone.localdate()
        holding.save()

    event = TradeEvent.objects.create(
        user=holding.user,
        holding=holding,
        trade_at=holding.opened_at or timezone.localdate(),
        opened_at=holding.opened_at,
        event_type=_build_event_type(holding),
        side=holding.side,
        ticker=holding.ticker,
        name=holding.name,
        qty=int(new_obj.quantity or 0),
        price=Decimal(new_obj.avg_cost or 0),
        basis=Decimal(new_obj.avg_cost or 0),
        fee=Decimal("0"),
        tax=Decimal("0"),
        broker=holding.broker,
        account=holding.account,
        country=holding.market,
        currency=holding.currency,
        fx_rate=holding.fx_rate,
        open_fx_rate=holding.fx_rate,
        close_fx_rate=holding.fx_rate,
        memo=f"保有登録: {holding.ticker}",
        position_key=f"{holding.ticker}-{(holding.opened_at or timezone.localdate()).isoformat()}-{holding.account}",
    )

    upsert_trade_event_ledger(event)
    return HoldingEntryResult(holding=holding, trade_event=event, merged=bool(merged))


@transaction.atomic
def update_holding_metadata(instance: Holding, cleaned_obj: Holding) -> Holding:
    """
    安全性のため、編集では metadata だけ許可する。
    数量/単価/口座区分/side は編集で変えず、イベントで処理する。
    """
    immutable_fields = [
        "ticker",
        "broker",
        "account",
        "side",
        "quantity",
        "avg_cost",
        "market",
        "currency",
        "fx_rate",
    ]

    for field in immutable_fields:
        if getattr(instance, field) != getattr(cleaned_obj, field):
            raise ValidationError(
                f"{field} は直接編集できません。削除して再入力するか、専用アクションで処理してください。"
            )

    instance.name = cleaned_obj.name
    instance.sector = cleaned_obj.sector
    instance.memo = cleaned_obj.memo
    instance.opened_at = cleaned_obj.opened_at
    instance.save()

    open_events = instance.trade_events.filter(
        realized_trade__isnull=True,
        event_type__in=[TradeEvent.EventType.SPOT_BUY, TradeEvent.EventType.MARGIN_OPEN],
    ).order_by("trade_at", "id")

    for ev in open_events:
        ev.opened_at = instance.opened_at
        if instance.opened_at:
            ev.trade_at = instance.opened_at
        ev.name = instance.name
        ev.save()

    return instance