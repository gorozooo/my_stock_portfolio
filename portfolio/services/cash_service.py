# -*- coding: utf-8 -*-
from __future__ import annotations
from datetime import date
from collections import defaultdict
from typing import Optional

from django.db import transaction
from django.db.models import Sum, F, DecimalField, ExpressionWrapper, Q

from ..models_cash import BrokerAccount, CashLedger, MarginState

# ==== Holding モデルを安全に import ====
try:
    from ..models import Holding, Dividend, RealizedTrade  # type: ignore
except Exception:
    Holding = None          # type: ignore
    Dividend = None         # type: ignore
    RealizedTrade = None    # type: ignore

# ---- ブローカー対応表（日本語⇄コード） --------------------
BROKER_JA_TO_CODE = {"楽天": "RAKUTEN", "松井": "MATSUI", "SBI": "SBI"}
BROKER_CODE_TO_JA = {v: k for k, v in BROKER_JA_TO_CODE.items()}

# ---- 初期口座を自動作成 --------------------
DEFAULT_BROKERS = ["楽天", "松井", "SBI"]

def ensure_default_accounts(currency: str = "JPY") -> list[BrokerAccount]:
    """初回アクセス時に代表口座（現物）を自動作成"""
    created = []
    for broker in DEFAULT_BROKERS:
        acc, was_created = BrokerAccount.objects.get_or_create(
            broker=broker, account_type="現物", currency=currency,
            defaults={"opening_balance": 0, "name": ""}
        )
        if was_created:
            created.append(acc)
    return created


# ---- 基本集計 ---------------------------------
def cash_balance(account: BrokerAccount) -> int:
    agg = CashLedger.objects.filter(account=account).aggregate(s=Sum("amount"))["s"] or 0
    return int(account.opening_balance + agg)

def month_netflow(account: BrokerAccount, year: int, month: int) -> int:
    qs = CashLedger.objects.filter(account=account, at__year=year, at__month=month)
    agg = qs.aggregate(s=Sum("amount"))["s"] or 0
    return int(agg)

def latest_margin(account: BrokerAccount) -> MarginState | None:
    return MarginState.objects.filter(account=account).order_by("-as_of").first()


# ---- 取得原価残（特定/NISAの未売却分） ----------------------
def acquisition_cost_remaining_for_broker(broker_ja: str) -> int:
    """
    指定“日本語ブローカー名”の、未売却の現物（特定/NISA）について
    平均取得単価×残数量 の合計（=取得原価残）を返す。
    """
    if Holding is None:
        return 0

    code = BROKER_JA_TO_CODE.get(broker_ja)
    if not code:
        return 0

    try:
        qs = Holding.objects.filter(
            broker=code,
            account__in=["SPEC", "NISA"],
            quantity__gt=0,
        )
        expr = ExpressionWrapper(F("quantity") * F("avg_cost"),
                                 output_field=DecimalField(max_digits=20, decimal_places=2))
        total = qs.aggregate(total=Sum(expr))["total"] or 0
        return int(total)
    except Exception:
        return 0


# ---- 口座単位の集計 --------------------------
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

    invested_cost = acquisition_cost_remaining_for_broker(account.broker)

    # 余力 = 現金 + 担保 - 拘束 - 取得原価残（マイナス許容）
    available = int(bal + collateral_usable - restricted - invested_cost)

    return {
        "broker": account.broker,
        "key": f"{account.broker}-{account.account_type}",
        "name": f"{account.broker} / {account.account_type}",
        "cash": int(bal),
        "restricted": int(restricted),
        "available": int(available),
        "currency": account.currency,
        "month_net": month_netflow(account, today.year, today.month),
        "invested_cost": int(invested_cost),
        "collateral_usable": int(collateral_usable),
    }


# ---- 全体KPI --------------------------------
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


# ---- ブローカー別集計（画面用） --------------------------
PREF_ORDER = ["楽天", "松井", "SBI", "moomoo"]

def broker_summaries(today: date):
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
def create_ledger(
    account: BrokerAccount,
    amount: int,
    kind: int,
    memo: str = "",
    at: Optional[date] = None,
    source_type: Optional[int] = None,
    source_id: Optional[int] = None,
):
    """
    すべての台帳登録の共通入口。発生日 at を任意指定できる。
    """
    if at is None:
        at = date.today()
    return CashLedger.objects.create(
        account=account,
        amount=amount,
        kind=kind,
        memo=memo,
        at=at,
        source_type=source_type,
        source_id=source_id,
    )

def deposit(account: BrokerAccount, amount: int, memo: str = "入金", at: Optional[date] = None):
    assert amount > 0
    return create_ledger(account, amount, CashLedger.Kind.DEPOSIT, memo=memo, at=at)

def withdraw(account: BrokerAccount, amount: int, memo: str = "出金", at: Optional[date] = None):
    assert amount > 0
    return create_ledger(account, -amount, CashLedger.Kind.WITHDRAW, memo=memo, at=at)

@transaction.atomic
def transfer(src: BrokerAccount, dst: BrokerAccount, amount: int, memo: str = "口座間振替", at: Optional[date] = None):
    assert amount > 0 and src != dst
    if at is None:
        at = date.today()
    create_ledger(src, -amount, CashLedger.Kind.XFER_OUT, memo=memo, at=at)
    create_ledger(dst, +amount, CashLedger.Kind.XFER_IN,  memo=memo, at=at)


# ---- Ledger日付の正規化（受取日/売買日へ補正） ---------------
def _source_date_for(entry: CashLedger) -> Optional[date]:
    """
    Ledger の source_type/source_id から“本来の発生日”を返す。
    - 配当: Dividend.date
    - 実損: RealizedTrade.trade_at
    取得不可の場合は None
    """
    try:
        st = int(getattr(entry, "source_type", 0) or 0)
        sid = int(getattr(entry, "source_id", 0) or 0)
    except Exception:
        return None

    if st == int(CashLedger.SourceType.DIVIDEND) and Dividend:
        d = Dividend.objects.filter(id=sid).only("date").first()
        return d.date if d else None
    if st == int(CashLedger.SourceType.REALIZED) and RealizedTrade:
        x = RealizedTrade.objects.filter(id=sid).only("trade_at").first()
        return x.trade_at if x else None
    return None

def normalize_ledger_dates(max_rows: int = 2000) -> int:
    """
    受取日/売買日へ at を補正する。
    - ダッシュボード/台帳のGET直前に軽く回す想定
    - 差分のみ update。戻り値は更新件数
    """
    qs = CashLedger.objects.filter(
        Q(source_type=CashLedger.SourceType.DIVIDEND) |
        Q(source_type=CashLedger.SourceType.REALIZED)
    ).order_by("-id")[:max_rows]

    updated = 0
    for led in qs:
        src_date = _source_date_for(led)
        if src_date and led.at != src_date:
            CashLedger.objects.filter(id=led.id).update(at=src_date)
            updated += 1
    return updated