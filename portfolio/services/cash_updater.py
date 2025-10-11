# -*- coding: utf-8 -*-
from __future__ import annotations
from django.db import transaction
from django.db.models import Q

from ..models import Dividend, RealizedTrade, Holding
from ..models_cash import BrokerAccount, CashLedger
from . import cash_service as svc


# =============================
# Broker 正規化 & 口座解決
# =============================
def _norm_broker(code: str) -> str:
    if not code:
        return ""
    s = str(code).strip().upper()
    if "RAKUTEN" in s or "楽天" in s:
        return "RAKUTEN"
    if "MATSUI" in s or "松井" in s:
        return "MATSUI"
    if "SBI" in s:
        return "SBI"
    return "OTHER"


def _label_from_code(code: str) -> str:
    return {"RAKUTEN": "楽天", "MATSUI": "松井", "SBI": "SBI", "OTHER": "その他"}.get(code, code)


def _get_account(broker_code: str, currency: str = "JPY") -> BrokerAccount | None:
    svc.ensure_default_accounts(currency=currency)
    code = _norm_broker(broker_code)
    label = _label_from_code(code)
    return (
        BrokerAccount.objects.filter(currency=currency)
        .filter(Q(broker=code) | Q(broker=label))
        .order_by("id")
        .first()
    )


def _as_int(x) -> int:
    try:
        return int(round(float(x or 0)))
    except Exception:
        return 0


# =============================
# Holding 検索
# =============================
def _find_holding(broker: str, ticker: str) -> Holding | None:
    """
    優先順位：
    ① broker一致 + ticker一致 + 口座区分（特定/NISA）
    ② broker一致 + ticker一致
    ③ ticker一致
    """
    if not ticker:
        return None

    norm = _norm_broker(broker)
    name_ja = _label_from_code(norm)

    q = Q(ticker=ticker)
    qs = Holding.objects.filter(q)

    # ① 現物口座（特定/NISA）限定で優先
    qs1 = qs.filter(
        Q(broker__in=[norm, name_ja]),
        Q(account_type__in=["特定", "NISA"]),
    ).order_by("-updated_at", "-id")

    if qs1.exists():
        return qs1.first()

    # ② broker一致
    qs2 = qs.filter(broker__in=[norm, name_ja]).order_by("-updated_at", "-id")
    if qs2.exists():
        return qs2.first()

    # ③ tickerのみ一致
    qs3 = qs.order_by("-updated_at", "-id")
    return qs3.first() if qs3.exists() else None


# =============================
# 内部ヘルパ
# =============================
def _upsert_ledger(**defaults) -> bool:
    obj, created = CashLedger.objects.update_or_create(
        source_type=defaults["source_type"],
        source_id=defaults["source_id"],
        defaults=defaults,
    )
    return created


# =============================
# 同期ロジック
# =============================
def sync_from_dividends() -> dict:
    created = 0
    updated = 0

    for d in Dividend.objects.all():
        acc = _get_account(d.broker)
        if not acc:
            continue

        amount = _as_int(d.net_amount())
        if amount <= 0:
            continue

        holding = getattr(d, "holding", None) or _find_holding(d.broker, d.ticker)

        created_now = _upsert_ledger(
            account=acc,
            at=d.date,  # 支払日
            amount=amount,
            kind=CashLedger.Kind.DEPOSIT,
            memo=f"配当 {d.display_ticker or d.ticker or ''}".strip(),
            source_type=CashLedger.SourceType.DIVIDEND,
            source_id=d.id,
            holding=holding,
        )
        if created_now:
            created += 1
        else:
            updated += 1

    return {"created": created, "updated": updated}


def sync_from_realized() -> dict:
    created = 0
    updated = 0

    for r in RealizedTrade.objects.all():
        # 現物系のみ（特定 / NISA）
        if getattr(r, "account", "") not in ("SPEC", "NISA"):
            continue

        acc = _get_account(r.broker)
        if not acc:
            continue

        delta = _as_int(r.cashflow_effective)
        if delta == 0:
            continue

        kind = CashLedger.Kind.DEPOSIT if delta > 0 else CashLedger.Kind.WITHDRAW
        holding = _find_holding(r.broker, r.ticker)

        created_now = _upsert_ledger(
            account=acc,
            at=r.trade_at,  # 取引日
            amount=delta,
            kind=kind,
            memo=f"実現損益 {r.ticker}".strip(),
            source_type=CashLedger.SourceType.REALIZED,
            source_id=r.id,
            holding=holding,
        )
        if created_now:
            created += 1
        else:
            updated += 1

    return {"created": created, "updated": updated}


# =============================
# 統合呼び出し
# =============================
@transaction.atomic
def sync_all() -> dict:
    svc.ensure_default_accounts()

    res_div = sync_from_dividends()
    res_real = sync_from_realized()

    return {
        "dividends_created": res_div["created"],
        "dividends_updated": res_div["updated"],
        "realized_created": res_real["created"],
        "realized_updated": res_real["updated"],
    }