# [FILE] balance_service.py
# [PATH] portfolio/services/cash/balance_service.py
#
# このファイルは何？
# - 現金残高 / 余力 / 現物総資産 / 月次入出金の集計専用サービス
#
# 今回の方針
# - 余力 = 現金 + 担保 - 拘束
# - メーターは「現物総資産に対して現金が何%残っているか」を表示
# - 現物総資産 = 現金 + 現物保有評価額
# - 月次は 差額 / 入金合計 / 出金合計 を返す

# -*- coding: utf-8 -*-
from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Optional

from django.db import transaction
from django.db.models import Q, Sum

from ...models import Holding, Dividend, RealizedTrade, TradeEvent
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


def month_deposit_total(account: BrokerAccount, year: int, month: int) -> int:
    agg = (
        CashLedger.objects.filter(
            account=account,
            at__year=year,
            at__month=month,
            kind=CashLedger.Kind.DEPOSIT,
        ).aggregate(s=Sum("amount"))["s"]
        or 0
    )
    return int(agg)


def month_withdraw_total(account: BrokerAccount, year: int, month: int) -> int:
    agg = (
        CashLedger.objects.filter(
            account=account,
            at__year=year,
            at__month=month,
            kind=CashLedger.Kind.WITHDRAW,
        ).aggregate(s=Sum("amount"))["s"]
        or 0
    )
    # WITHDRAW は台帳にマイナスで入っているので、表示用に絶対値に直す
    return abs(int(agg))


def latest_margin(account: BrokerAccount) -> MarginState | None:
    return MarginState.objects.filter(account=account).order_by("-as_of").first()


def _holding_broker_q(broker_ja: str) -> Q:
    code = BROKER_JA_TO_CODE.get((broker_ja or "").strip(), "")
    if code:
        return Q(broker=broker_ja) | Q(broker=code)
    return Q(broker=broker_ja)


def _to_float(v) -> float:
    try:
        return float(v or 0)
    except Exception:
        return 0.0


def acquisition_cost_remaining_for_broker(broker_ja: str) -> int:
    """
    指定ブローカーの未売却現物（特定 / NISA）の取得原価残（JPY換算）を返す。
    ※ 今回の画面では直接表示しないが、互換のため返せるようにしておく。
    """
    try:
        qs = Holding.objects.filter(
            _holding_broker_q(broker_ja),
            account__in=["SPEC", "NISA"],
            quantity__gt=0,
        ).only("quantity", "avg_cost", "currency", "fx_rate")

        total = 0.0
        for h in qs:
            qty = int(h.quantity or 0)
            avg_cost = _to_float(h.avg_cost)
            cur = (getattr(h, "currency", "") or "JPY").upper()
            fx = 1.0 if cur == "JPY" else max(_to_float(getattr(h, "fx_rate", None)), 1.0)
            total += qty * avg_cost * fx
        return int(round(total))
    except Exception:
        return 0


def spot_market_value_for_broker(broker_ja: str) -> int:
    """
    指定ブローカーの現物（特定 / NISA）保有評価額（JPY換算）を返す。
    - last_price があればそれを優先
    - 無ければ avg_cost を使う
    - USD など外貨は holding.fx_rate で JPY 換算
    """
    try:
        qs = Holding.objects.filter(
            _holding_broker_q(broker_ja),
            account__in=["SPEC", "NISA"],
            quantity__gt=0,
        ).only("quantity", "last_price", "avg_cost", "currency", "fx_rate")

        total = 0.0
        for h in qs:
            qty = int(h.quantity or 0)
            last_price = _to_float(getattr(h, "last_price", None))
            avg_cost = _to_float(getattr(h, "avg_cost", None))
            unit_price = last_price if last_price > 0 else avg_cost

            cur = (getattr(h, "currency", "") or "JPY").upper()
            fx = 1.0 if cur == "JPY" else max(_to_float(getattr(h, "fx_rate", None)), 1.0)

            total += qty * unit_price * fx

        return int(round(total))
    except Exception:
        return 0


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

    invested_cost = acquisition_cost_remaining_for_broker(account.broker)
    spot_market_value = spot_market_value_for_broker(account.broker)

    # 現物総資産 = 現金 + 現物保有評価額
    spot_total_asset = int(bal + spot_market_value)

    # 現物総資産に対して現金が何%残っているか
    cash_ratio_pct = None
    if spot_total_asset > 0:
        cash_ratio_pct = (bal / spot_total_asset) * 100.0

    month_dep = month_deposit_total(account, today.year, today.month)
    month_wd = month_withdraw_total(account, today.year, today.month)
    month_net = int(month_dep - month_wd)

    return {
        "broker": account.broker,
        "key": f"{account.broker}-{account.account_type}",
        "name": f"{account.broker} / {account.account_type}",
        "cash": int(bal),
        "restricted": int(restricted),
        "available": int(available),
        "currency": account.currency,
        "month_net": int(month_net),
        "month_deposit": int(month_dep),
        "month_withdraw": int(month_wd),
        "collateral_usable": int(collateral_usable),
        "invested_cost": int(invested_cost),
        "spot_market_value": int(spot_market_value),
        "spot_total_asset": int(spot_total_asset),
        "cash_ratio_pct": cash_ratio_pct,
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
        "month_deposit": sum(r["month_deposit"] for r in rows) if rows else 0,
        "month_withdraw": sum(r["month_withdraw"] for r in rows) if rows else 0,
        "invested_cost": sum(r["invested_cost"] for r in rows) if rows else 0,
        "spot_market_value": sum(r["spot_market_value"] for r in rows) if rows else 0,
        "spot_total_asset": sum(r["spot_total_asset"] for r in rows) if rows else 0,
    }
    return total, rows


PREF_ORDER = ["楽天", "松井", "SBI", "moomoo"]


def broker_summaries(today: date):
    ensure_default_accounts()
    acc_rows = [account_summary(acc, today) for acc in BrokerAccount.objects.all()]

    grouped = defaultdict(
        lambda: {
            "cash": 0,
            "restricted": 0,
            "available": 0,
            "month_net": 0,
            "month_deposit": 0,
            "month_withdraw": 0,
            "invested_cost": 0,
            "spot_market_value": 0,
            "spot_total_asset": 0,
        }
    )

    for r in acc_rows:
        g = grouped[r["broker"]]
        g["cash"] += r["cash"]
        g["restricted"] += r["restricted"]
        g["available"] += r["available"]
        g["month_net"] += r["month_net"]
        g["month_deposit"] += r["month_deposit"]
        g["month_withdraw"] += r["month_withdraw"]
        g["invested_cost"] += r["invested_cost"]
        g["spot_market_value"] += r["spot_market_value"]
        g["spot_total_asset"] += r["spot_total_asset"]

    items = []
    for broker, v in grouped.items():
        cash = int(v["cash"])
        available = int(v["available"])
        spot_total_asset = int(v["spot_total_asset"])

        cash_ratio_pct = None
        if spot_total_asset > 0:
            cash_ratio_pct = (cash / spot_total_asset) * 100.0

        pct_available = None
        if cash > 0:
            pct_available = (available / cash) * 100.0

        severity = "ok"
        if available < 0:
            severity = "danger"
        elif cash > 0 and available / cash < 0.30:
            severity = "warn"

        items.append(
            {
                "broker": broker,
                "cash": cash,
                "restricted": int(v["restricted"]),
                "available": available,
                "month_net": int(v["month_net"]),
                "month_deposit": int(v["month_deposit"]),
                "month_withdraw": int(v["month_withdraw"]),
                "invested_cost": int(v["invested_cost"]),
                "spot_market_value": int(v["spot_market_value"]),
                "spot_total_asset": spot_total_asset,
                "cash_ratio_pct": cash_ratio_pct,
                "pct_available": pct_available,
                "severity": severity,
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