# [FILE] balance_service.py
# [PATH] portfolio/services/cash/balance_service.py
#
# このファイルは何？
# - 現金残高/余力の集計専用サービス
#
# 今回の方針
# - 余力 = 現金 + 担保 - 拘束
# - invested_cost は余力に使わない
# - CashLedger は「実際の現金」をそのまま合計する

# -*- coding: utf-8 -*-
from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Optional

from django.db import transaction
from django.db.models import Q, Sum

from ...models import Dividend, RealizedTrade, TradeEvent
from ...models_cash import BrokerAccount, CashLedger, MarginState

BROKER_JA_TO_CODE = {"楽天": "RAKUTEN", "松井": "MATSUI", "SBI": "SBI"}
BROKER_CODE_TO_JA = {v: k for k, v in BROKER_JA_TO_CODE.items()}

DEFAULT_BROKERS = ["楽天", "松井", "SBI"]


def ensure_default_accounts(currency: str = "JPY") -> list[BrokerAccount]:
    """
    既存UI互換のため、現時点では broker ごとに「現物」口座を代表財布として使う。
    """
    created = []
    for broker in DEFAULT_BROKERS:
        acc, was_created = BrokerAccount.objects.get_or_create(
            broker=broker,
            account_type="現物",
            currency=currency,
            defaults={"opening_balance": 0, "name": ""},
        )
        if was_created:
            created.append(acc)
    return created


def _broker_label(raw: str) -> str:
    s = (raw or "").strip()
    if s in DEFAULT_BROKERS:
        return s
    code = s.upper()
    return BROKER_CODE_TO_JA.get(code, s or "楽天")


def get_cash_account_for_broker(broker: str, currency: str = "JPY") -> BrokerAccount | None:
    ensure_default_accounts(currency=currency)
    label = _broker_label(broker)
    return (
        BrokerAccount.objects.filter(broker=label, account_type="現物", currency=currency)
        .order_by("id")
        .first()
    )


def cash_balance(account: BrokerAccount) -> int:
    agg = CashLedger.objects.filter(account=account).aggregate(s=Sum("amount"))["s"] or 0
    return int(account.opening_balance + agg)


def month_netflow(account: BrokerAccount, year: int, month: int) -> int:
    agg = (
        CashLedger.objects.filter(account=account, at__year=year, at__month=month)
        .aggregate(s=Sum("amount"))["s"]
        or 0
    )
    return int(agg)


def latest_margin(account: BrokerAccount) -> MarginState | None:
    return MarginState.objects.filter(account=account).order_by("-as_of").first()


def account_summary(account: BrokerAccount, today: date):
    bal = cash_balance(account)
    m = latest_margin(account)

    collateral_usable = 0
    restricted = 0
    if m:
        collateral_usable = int(getattr(m, "collateral_usable", 0) or 0)
        required_margin = int(getattr(m, "required_margin", 0) or 0)
        restricted_amount = int(getattr(m, "restricted_amount", 0) or 0)
        restricted = required_margin + restricted_amount

    available = int(bal + collateral_usable - restricted)

    return {
        "broker": account.broker,
        "key": f"{account.broker}-{account.account_type}",
        "name": f"{account.broker} / {account.account_type}",
        "cash": int(bal),
        "restricted": int(restricted),
        "available": int(available),
        "currency": account.currency,
        "month_net": month_netflow(account, today.year, today.month),
        "collateral_usable": int(collateral_usable),
    }


def total_summary(today: date):
    rows = []
    for acc in BrokerAccount.objects.all().order_by("broker", "account_type"):
        rows.append(account_summary(acc, today))

    total = {
        "available": sum(r["available"] for r in rows) if rows else 0,
        "cash_total": sum(r["cash"] for r in rows) if rows else 0,
        "restricted": sum(r["restricted"] for r in rows) if rows else 0,
        "month_net": sum(r["month_net"] for r in rows) if rows else 0,
    }
    return total, rows


PREF_ORDER = ["楽天", "松井", "SBI", "moomoo"]


def broker_summaries(today: date):
    ensure_default_accounts()
    acc_rows = [account_summary(acc, today) for acc in BrokerAccount.objects.all()]

    grouped = defaultdict(lambda: {"cash": 0, "restricted": 0, "available": 0, "month_net": 0})
    for r in acc_rows:
        g = grouped[r["broker"]]
        g["cash"] += r["cash"]
        g["restricted"] += r["restricted"]
        g["available"] += r["available"]
        g["month_net"] += r["month_net"]

    items = []
    for broker, v in grouped.items():
        items.append(
            {
                "broker": broker,
                "cash": int(v["cash"]),
                "restricted": int(v["restricted"]),
                "available": int(v["available"]),
                "month_net": int(v["month_net"]),
            }
        )

    pref_index = {b: i for i, b in enumerate(PREF_ORDER)}
    items.sort(key=lambda x: (pref_index.get(x["broker"], 999), x["broker"]))
    return items


def create_ledger(
    account: BrokerAccount,
    amount: int,
    kind: str,
    memo: str = "",
    at: Optional[date] = None,
    source_type: Optional[str] = None,
    source_id: Optional[int] = None,
):
    if at is None:
        at = date.today()
    return CashLedger.objects.create(
        account=account,
        amount=int(amount),
        kind=kind,
        memo=memo,
        at=at,
        source_type=source_type,
        source_id=source_id,
    )


def deposit(account: BrokerAccount, amount: int, memo: str = "入金", at: Optional[date] = None):
    assert int(amount) > 0
    return create_ledger(account, int(amount), CashLedger.Kind.DEPOSIT, memo=memo, at=at)


def withdraw(account: BrokerAccount, amount: int, memo: str = "出金", at: Optional[date] = None):
    assert int(amount) > 0
    return create_ledger(account, -int(amount), CashLedger.Kind.WITHDRAW, memo=memo, at=at)


@transaction.atomic
def transfer(src: BrokerAccount, dst: BrokerAccount, amount: int, memo: str = "口座間振替", at: Optional[date] = None):
    assert int(amount) > 0 and src != dst
    if at is None:
        at = date.today()
    create_ledger(src, -int(amount), CashLedger.Kind.XFER_OUT, memo=memo, at=at)
    create_ledger(dst, int(amount), CashLedger.Kind.XFER_IN, memo=memo, at=at)


def _source_date_for(entry: CashLedger) -> Optional[date]:
    st = (entry.source_type or "").upper()
    sid = entry.source_id

    if not sid:
        return None

    if st == CashLedger.SourceType.DIVIDEND:
        d = Dividend.objects.filter(id=sid).only("date").first()
        return d.date if d else None

    if st == CashLedger.SourceType.TRADE_EVENT:
        t = TradeEvent.objects.filter(id=sid).only("trade_at").first()
        return t.trade_at if t else None

    if st == CashLedger.SourceType.REALIZED:
        x = RealizedTrade.objects.filter(id=sid).only("trade_at").first()
        return x.trade_at if x else None

    return None


def normalize_ledger_dates(max_rows: int = 4000) -> int:
    qs = (
        CashLedger.objects.filter(
            Q(source_type=CashLedger.SourceType.DIVIDEND)
            | Q(source_type=CashLedger.SourceType.TRADE_EVENT)
            | Q(source_type=CashLedger.SourceType.REALIZED)
        )
        .order_by("-id")[:max_rows]
    )

    updated = 0
    for led in qs:
        src_date = _source_date_for(led)
        if src_date and led.at != src_date:
            CashLedger.objects.filter(id=led.id).update(at=src_date)
            updated += 1
    return updated