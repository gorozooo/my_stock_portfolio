# [FILE] cash_updater.py
# [PATH] portfolio/services/cash_updater.py
#
# このファイルは何？
# - Dividend / TradeEvent から CashLedger を再同期するサービス
#
# 今回の方針
# - Holding から現金を逆算しない
# - RealizedTrade 単体からも現金を作らない
# - 現金の source-of-truth は TradeEvent と Dividend

# -*- coding: utf-8 -*-
from __future__ import annotations

from django.db import transaction
from django.db.models import Q

from ..models import Dividend, TradeEvent
from ..models_cash import CashLedger
from . import cash_service as svc
from .cash.ledger_service import upsert_dividend_ledger, upsert_trade_event_ledger


def sync_from_dividends() -> dict:
    created = 0
    updated = 0

    for d in Dividend.objects.all():
        created_now = upsert_dividend_ledger(d)
        if created_now:
            created += 1
        else:
            updated += 1

    return {"created": created, "updated": updated}


def sync_from_trade_events() -> dict:
    created = 0
    updated = 0

    for t in TradeEvent.objects.all():
        created_now = upsert_trade_event_ledger(t)
        if created_now:
            created += 1
        else:
            updated += 1

    return {"created": created, "updated": updated}


@transaction.atomic
def sync_all() -> dict:
    svc.ensure_default_accounts()
    res_div = sync_from_dividends()
    res_trd = sync_from_trade_events()
    return {
        "dividends_created": res_div["created"],
        "dividends_updated": res_div["updated"],
        "trade_events_created": res_trd["created"],
        "trade_events_updated": res_trd["updated"],
    }


@transaction.atomic
def rebuild_all(clear_generated: bool = True) -> dict:
    """
    再生成用。
    clear_generated=True のとき、生成系の台帳を消してから作り直す。
    """
    svc.ensure_default_accounts()

    deleted = 0
    if clear_generated:
        qs = CashLedger.objects.filter(
            Q(source_type=CashLedger.SourceType.DIVIDEND)
            | Q(source_type=CashLedger.SourceType.TRADE_EVENT)
            | Q(source_type=CashLedger.SourceType.REALIZED)
            | Q(source_type=CashLedger.SourceType.HOLDING)
        )
        deleted = qs.count()
        qs.delete()

    res = sync_all()
    res["deleted_generated_ledgers"] = deleted
    return res