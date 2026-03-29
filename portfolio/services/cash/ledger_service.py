# [FILE] ledger_service.py
# [PATH] portfolio/services/cash/ledger_service.py
#
# このファイルは何？
# - TradeEvent / Dividend から CashLedger を作るサービス
#
# 今回の方針
# - CashLedger は実際の現金だけ
# - Spot Buy/Sell は受渡額フル
# - Margin Close は損益だけ
# - Margin Open は現金を動かさない
# - 現引は現金出金として扱う

# -*- coding: utf-8 -*-
from __future__ import annotations

from ...models import Dividend, TradeEvent
from ...models_cash import CashLedger
from .balance_service import get_cash_account_for_broker


def _dividend_memo(d: Dividend) -> str:
    return f"配当 {d.display_ticker or d.ticker or ''}".strip()


def _trade_memo(t: TradeEvent) -> str:
    label = {
        TradeEvent.EventType.SPOT_BUY: "現物買付",
        TradeEvent.EventType.SPOT_SELL: "現物売却",
        TradeEvent.EventType.MARGIN_OPEN: "信用新規",
        TradeEvent.EventType.MARGIN_CLOSE: "信用返済",
        TradeEvent.EventType.MARGIN_TO_SPOT: "現引",
    }.get(t.event_type, "売買")
    return f"{label} {t.ticker}".strip()


def delete_trade_event_ledgers(event_id: int) -> int:
    qs = CashLedger.objects.filter(
        source_type=CashLedger.SourceType.TRADE_EVENT,
        source_id=event_id,
    )
    count = qs.count()
    qs.delete()
    return count


def upsert_dividend_ledger(d: Dividend) -> bool:
    acc = get_cash_account_for_broker(d.broker)
    if not acc:
        return False

    amount = int(round(float(d.net_amount() or 0)))
    if amount <= 0:
        return False

    _, created = CashLedger.objects.update_or_create(
        account=acc,
        source_type=CashLedger.SourceType.DIVIDEND,
        source_id=d.id,
        defaults={
            "at": d.date,
            "amount": amount,
            "kind": CashLedger.Kind.DEPOSIT,
            "memo": _dividend_memo(d),
            "holding": getattr(d, "holding", None),
        },
    )
    return created


def upsert_trade_event_ledger(t: TradeEvent) -> bool:
    """
    TradeEvent から CashLedger を作る。
    - MARGIN_OPEN は cash=0 のため ledger を作らない
    - その他は cash_amount_jpy をそのまま使う
    """
    acc = get_cash_account_for_broker(t.broker)
    if not acc:
        return False

    cash_jpy = int(t.cash_amount_jpy or 0)

    if t.event_type == TradeEvent.EventType.MARGIN_OPEN or cash_jpy == 0:
        delete_trade_event_ledgers(t.id)
        return False

    kind = CashLedger.Kind.DEPOSIT if cash_jpy > 0 else CashLedger.Kind.WITHDRAW

    _, created = CashLedger.objects.update_or_create(
        account=acc,
        source_type=CashLedger.SourceType.TRADE_EVENT,
        source_id=t.id,
        defaults={
            "at": t.trade_at,
            "amount": cash_jpy,
            "kind": kind,
            "memo": _trade_memo(t),
            "holding": t.holding,
        },
    )
    return created