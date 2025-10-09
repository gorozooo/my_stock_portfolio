# portfolio/services/cash_service.py
from __future__ import annotations
from datetime import date
from collections import defaultdict
from django.db.models import Sum, F, DecimalField, ExpressionWrapper
from django.db import transaction
from ..models_cash import BrokerAccount, CashLedger, MarginState

# ==== Holding モデルを安全に import ====
try:
    from ..models import Holding  # type: ignore
except Exception:
    Holding = None  # type: ignore

# ---- ブローカー対応表（日本語⇄コード） --------------------
BROKER_JA_TO_CODE = {"楽天": "RAKUTEN", "松井": "MATSUI", "SBI": "SBI"}
# BROKER_CODE_TO_JA = {v: k for k, v in BROKER_JA_TO_CODE.items()}

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


# ---- 取得原価残（特定/NISAの未売却分）※ブローカー単位で一度だけ使う ----
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
        expr = ExpressionWrapper(
            F("quantity") * F("avg_cost"),
            output_field=DecimalField(max_digits=20, decimal_places=2),
        )
        total = qs.aggregate(total=Sum(expr))["total"] or 0
        return int(total)
    except Exception:
        return 0


# ---- 口座単位の集計（取得原価はここでは引かない） --------------------------
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

    # 原価控除前の余力（= 現金 + 担保 - 拘束）
    available_base = int(bal + collateral_usable - restricted)

    return {
        "broker": account.broker,
        "key": f"{account.broker}-{account.account_type}",
        "name": f"{account.broker} / {account.account_type}",
        "cash": int(bal),
        "restricted": int(restricted),
        # 互換のためキー名は 'available' を維持するが、中身は「原価控除前」
        "available": int(available_base),
        "currency": account.currency,
        "month_net": month_netflow(account, today.year, today.month),
        "collateral_usable": int(collateral_usable),
    }


# ---- ブローカー別集計（画面用：ここで原価を一度だけ控除） ------------------
PREF_ORDER = ["楽天", "松井", "SBI", "moomoo"]

def broker_summaries(today: date):
    ensure_default_accounts()

    acc_rows = [account_summary(acc, today) for acc in BrokerAccount.objects.all()]

    # まずは原価控除「前」の合算
    grouped = defaultdict(lambda: {"cash": 0, "restricted": 0, "available_base": 0, "month_net": 0})
    for r in acc_rows:
        g = grouped[r["broker"]]
        g["cash"]           += r["cash"]
        g["restricted"]     += r["restricted"]
        g["available_base"] += r["available"]  # ← base
        g["month_net"]      += r["month_net"]

    # ブローカー単位で取得原価残を一度だけ差し引く
    items = []
    for broker, v in grouped.items():
        invested_cost = acquisition_cost_remaining_for_broker(broker)
        available = int(v["available_base"] - invested_cost)
        items.append({
            "broker": broker,
            "cash": int(v["cash"]),
            "restricted": int(v["restricted"]),
            "available": int(available),
            "month_net": int(v["month_net"]),
            "invested_cost": int(invested_cost),
        })

    pref_index = {b: i for i, b in enumerate(PREF_ORDER)}
    items.sort(key=lambda x: (pref_index.get(x["broker"], 999), x["broker"]))
    return items


# ---- 全体KPI（総計もブローカーごとに原価を一度だけ控除） -------------------
def total_summary(today: date):
    # ブローカー別を元にすれば重複控除が起きない
    brokers = broker_summaries(today)
    total = {
        "available": sum(b["available"] for b in brokers) if brokers else 0,
        "cash_total": sum(b["cash"] for b in brokers) if brokers else 0,
        "restricted": sum(b["restricted"] for b in brokers) if brokers else 0,
        "month_net": sum(b["month_net"] for b in brokers) if brokers else 0,
        # 参考：全ブローカーの取得原価残合計
        "invested_cost_total": sum(b.get("invested_cost", 0) for b in brokers) if brokers else 0,
    }
    return total, brokers  # rows はブローカー粒度を返す


# ---- 台帳操作 ---------------------------------------------
def deposit(account: BrokerAccount, amount: int, memo: str = "入金"):
    assert amount > 0
    return CashLedger.objects.create(
        account=account, amount=amount, kind=CashLedger.Kind.DEPOSIT, memo=memo
    )

def withdraw(account: BrokerAccount, amount: int, memo: str = "出金"):
    assert amount > 0
    return CashLedger.objects.create(
        account=account, amount=-amount, kind=CashLedger.Kind.WITHDRAW, memo=memo
    )

@transaction.atomic
def transfer(src: BrokerAccount, dst: BrokerAccount, amount: int, memo: str = "口座間振替"):
    assert amount > 0 and src != dst
    CashLedger.objects.create(account=src, amount=-amount, kind=CashLedger.Kind.XFER_OUT, memo=memo)
    CashLedger.objects.create(account=dst, amount=+amount, kind=CashLedger.Kind.XFER_IN,  memo=memo)