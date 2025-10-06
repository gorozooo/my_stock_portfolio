# portfolio/services/cash_updater.py
from __future__ import annotations
from django.db import transaction, IntegrityError

from portfolio.models import Dividend, RealizedTrade
from ..models_cash import BrokerAccount, CashLedger
from . import cash_service as svc


# ---- Broker 正規化 ------------------------------------------------
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
    try:
        return int(round(float(x or 0)))
    except Exception:
        return 0


# ---- 内部ヘルパ：セーブポイント付き create --------------------
def _create_ledger_safe(**kwargs) -> bool:
    """
    1レコードの作成を savepoint で隔離。
    IntegrityError はロールバックして False を返す（外側トランザクションを壊さない）。
    """
    try:
        with transaction.atomic():  # savepoint=True デフォルト
            CashLedger.objects.create(**kwargs)
        return True
    except IntegrityError:
        return False


# ---- 同期本体 ----------------------------------------------------
def sync_from_dividends() -> int:
    created = 0
    # ★ iterator() は使わず、あらかじめ全件 list() 化してカーソルを閉じておく
    dividends = list(Dividend.objects.all())
    for d in dividends:
        acc = _get_account(d.broker)
        if not acc:
            continue
        amount = _as_int(d.net_amount())  # UIは税引後前提
        if amount <= 0:
            continue

        ok = _create_ledger_safe(
            account=acc,
            amount=amount,
            kind=CashLedger.Kind.DEPOSIT,
            memo=f"配当 DIV:{d.id}",
            source_type=CashLedger.SourceType.DIVIDEND,
            source_id=d.id,
        )
        if ok:
            created += 1
    return created


def sync_from_realized() -> int:
    created = 0
    realized = list(RealizedTrade.objects.all())  # ← ここも iterator() 禁止！
    for r in realized:
        acc = _get_account(r.broker)
        if not acc:
            continue
        delta = _as_int(r.cashflow_effective)  # SELL=＋ / BUY=− / 料税込み
        if delta == 0:
            continue

        kind = CashLedger.Kind.DEPOSIT if delta > 0 else CashLedger.Kind.WITHDRAW
        ok = _create_ledger_safe(
            account=acc,
            amount=delta,
            kind=kind,
            memo=f"実現損益 REAL:{r.id}",
            source_type=CashLedger.SourceType.REALIZED,
            source_id=r.id,
        )
        if ok:
            created += 1
    return created


def sync_all() -> dict:
    """
    外側では atomic を張らない。
    iterator() を使わず、savepoint 内で安全に1件ずつ insert。
    """
    d = sync_from_dividends()
    r = sync_from_realized()
    return {"dividends_created": d, "realized_created": r}