# portfolio/services/cash_service.py
from __future__ import annotations
from datetime import date
from django.db.models import Sum, Q
from django.db import transaction
from ..models.cash import BrokerAccount, CashLedger, MarginState

def cash_balance(account: BrokerAccount) -> int:
    agg = CashLedger.objects.filter(account=account).aggregate(s=Sum("amount"))["s"] or 0
    return int(account.opening_balance + agg)

def month_netflow(account: BrokerAccount, year: int, month: int) -> int:
    qs = CashLedger.objects.filter(account=account, at__year=year, at__month=month)
    # 入出金・振替・配当・手数料・税など全部込みのネット
    agg = qs.aggregate(s=Sum("amount"))["s"] or 0
    return int(agg)

def latest_margin(account: BrokerAccount) -> MarginState | None:
    return MarginState.objects.filter(account=account).order_by("-as_of").first()

def account_summary(account: BrokerAccount, today: date):
    bal = cash_balance(account)
    m = latest_margin(account)
    restricted = 0
    available = bal
    if m:
        # “自由現金”が管理画面で入力されるならそちら優先
        # 未入力でも最低限 bal を土台に余力を近似できる
        collateral_usable = m.collateral_usable
        restricted = int(m.required_margin + m.restricted_amount)
        available = int(m.cash_free + collateral_usable - restricted)
        # cash_free未入力なら、簡易的に bal を自由現金として扱う
        if m.cash_free == 0:
            available = int(bal + collateral_usable - restricted)

    return {
        "key": f"{account.broker}-{account.account_type}",
        "name": f"{account.broker} / {account.account_type}",
        "cash": bal,
        "restricted": restricted,
        "available": max(available, 0),
        "currency": account.currency,
        "month_net": month_netflow(account, today.year, today.month),
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

# ーーー 台帳操作（入金/出金/振替）ーーー
def deposit(account: BrokerAccount, amount: int, memo: str = "入金"):
    assert amount > 0
    return CashLedger.objects.create(account=account, amount=amount, kind=CashLedger.Kind.DEPOSIT, memo=memo)

def withdraw(account: BrokerAccount, amount: int, memo: str = "出金"):
    assert amount > 0
    return CashLedger.objects.create(account=account, amount=-amount, kind=CashLedger.Kind.WITHDRAW, memo=memo)

@transaction.atomic
def transfer(src: BrokerAccount, dst: BrokerAccount, amount: int, memo: str = "口座間振替"):
    assert amount > 0 and src != dst
    CashLedger.objects.create(account=src, amount=-amount, kind=CashLedger.Kind.XFER_OUT, memo=memo)
    CashLedger.objects.create(account=dst, amount=+amount, kind=CashLedger.Kind.XFER_IN, memo=memo)