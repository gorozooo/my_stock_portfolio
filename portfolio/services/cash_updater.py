# portfolio/services/cash_updater.py
from __future__ import annotations
from django.db import transaction, IntegrityError

from portfolio.models import Dividend, RealizedTrade
from ..models_cash import BrokerAccount, CashLedger
from . import cash_service as svc

def _norm_broker(code: str) -> str:
    if not code:
        return ""
    s = str(code).strip().upper()
    if "RAKUTEN" in s or "楽天" in s: return "RAKUTEN"
    if "MATSUI"  in s or "松井" in s: return "MATSUI"
    if "SBI"     in s:               return "SBI"
    return "OTHER"

def _label_from_code(code: str) -> str:
    return {"RAKUTEN":"楽天","MATSUI":"松井","SBI":"SBI","OTHER":"その他"}.get(code, code)

def _get_account(broker_code: str, currency: str = "JPY") -> BrokerAccount | None:
    svc.ensure_default_accounts(currency=currency)
    code  = _norm_broker(broker_code)
    label = _label_from_code(code)
    qs = BrokerAccount.objects.filter(currency=currency)
    return qs.filter(broker=code).first() or qs.filter(broker=label).first()

def _as_int(x) -> int:
    try: return int(round(float(x or 0)))
    except Exception: return 0

def sync_from_dividends() -> int:
    created = 0
    for d in Dividend.objects.all().iterator():
        acc = _get_account(d.broker)
        if not acc:
            continue
        amount = _as_int(d.net_amount())  # UIは税引後前提
        if amount <= 0:
            continue
        try:
            # ユニーク制約で重複を防ぐ（source_type, source_id）
            CashLedger.objects.create(
                account=acc,
                amount= amount,
                kind=CashLedger.Kind.DEPOSIT,
                memo=f"配当 DIV:{d.id}",
                source_type=CashLedger.SourceType.DIVIDEND,
                source_id=d.id,
            )
            created += 1
        except IntegrityError:
            # 既に同じ source が入っている → スキップ
            pass
    return created

def sync_from_realized() -> int:
    created = 0
    for r in RealizedTrade.objects.all().iterator():
        acc = _get_account(r.broker)
        if not acc:
            continue
        delta = _as_int(r.cashflow_effective)
        if delta == 0:
            continue
        kind = CashLedger.Kind.DEPOSIT if delta > 0 else CashLedger.Kind.WITHDRAW
        signed_amount = delta  # 正:入金 / 負:出金（モデルが符号持ち運用）
        try:
            CashLedger.objects.create(
                account=acc,
                amount=signed_amount,
                kind=kind,
                memo=f"実現損益 REAL:{r.id}",
                source_type=CashLedger.SourceType.REALIZED,
                source_id=r.id,
            )
            created += 1
        except IntegrityError:
            pass
    return created

@transaction.atomic
def sync_all() -> dict:
    d = sync_from_dividends()
    r = sync_from_realized()
    return {"dividends_created": d, "realized_created": r}