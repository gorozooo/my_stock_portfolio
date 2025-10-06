# portfolio/services/cash_service.py
from __future__ import annotations
from datetime import date
from collections import defaultdict
from django.db.models import Sum
from django.db import transaction
from ..models_cash import BrokerAccount, CashLedger, MarginState

# ---- 初期口座を自動作成（楽天/松井/SBI） --------------------
DEFAULT_BROKERS = ["楽天", "松井", "SBI"]

def ensure_default_accounts(currency: str = "JPY") -> list[BrokerAccount]:
    """
    初回アクセス時に代表口座（現物）を自動作成する。
    既にあれば何もしない。
    """
    created = []
    for broker in DEFAULT_BROKERS:
        acc, was_created = BrokerAccount.objects.get_or_create(
            broker=broker, account_type="現物", currency=currency,
            defaults={"opening_balance": 0, "name": ""}
        )
        if was_created:
            created.append(acc)
    return created

# ---- 基本集計（口座単位） ---------------------------------
def cash_balance(account: BrokerAccount) -> int:
    agg = CashLedger.objects.filter(account=account).aggregate(s=Sum("amount"))["s"] or 0
    return int(account.opening_balance + agg)

def month_netflow(account: BrokerAccount, year: int, month: int) -> int:
    qs = CashLedger.objects.filter(account=account, at__year=year, at__month=month)
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
        collateral_usable = m.collateral_usable
        restricted = int(m.required_margin + m.restricted_amount)
        available = int(m.cash_free + collateral_usable - restricted)
        if m.cash_free == 0:
            available = int(bal + collateral_usable - restricted)

    return {
        "broker": account.broker,
        "key": f"{account.broker}-{account.account_type}",
        "name": f"{account.broker} / {account.account_type}",
        "cash": bal,
        "restricted": restricted,
        "available": max(available, 0),
        "currency": account.currency,
        "month_net": month_netflow(account, today.year, today.month),
    }

# ---- 全体KPI（ホーム統合用に残す） --------------------------
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

# ---- ★ 証券会社ごとの集計（画面の主役） ---------------------
PREF_ORDER = ["楽天", "松井", "SBI", "moomoo"]

def broker_summaries(today: date):
    """BrokerAccount を“証券会社名”でまとめたKPIリストを返す。"""
    # 代表口座が無ければ作る（初回アクセス対策）
    ensure_default_accounts()

    acc_rows = [account_summary(acc, today) for acc in BrokerAccount.objects.all()]
    grouped = defaultdict(lambda: {"cash":0,"restricted":0,"available":0,"month_net":0})
    for r in acc_rows:
        g = grouped[r["broker"]]
        g["cash"]       += r["cash"]
        g["restricted"] += r["restricted"]
        g["available"]  += r["available"]
        g["month_net"]  += r["month_net"]

    items = []
    for broker, v in grouped.items():
        items.append({
            "broker": broker,
            "cash": int(v["cash"]),
            "restricted": int(v["restricted"]),
            "available": int(v["available"]),
            "month_net": int(v["month_net"]),
        })

    pref_index = {b:i for i,b in enumerate(PREF_ORDER)}
    items.sort(key=lambda x: (pref_index.get(x["broker"], 999), x["broker"]))
    return items

# ---- 台帳操作 ---------------------------------------------
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