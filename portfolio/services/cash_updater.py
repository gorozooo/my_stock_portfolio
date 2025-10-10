# portfolio/services/cash_updater.py
# -*- coding: utf-8 -*-
from __future__ import annotations
from typing import Dict, Any, Optional

from django.db import transaction

from ..models import Dividend, RealizedTrade
from ..models_cash import BrokerAccount, CashLedger
from . import cash_service as svc

# --- ブローカー表記ゆれ対策 -----------------------------------------------
_CANON = {
    "RAKUTEN": "楽天",
    "楽天": "楽天",
    "楽天証券": "楽天",
    "MATSUI": "松井",
    "松井": "松井",
    "松井証券": "松井",
    "SBI": "SBI",
    "ＳＢＩ": "SBI",
    "SBI証券": "SBI",
}
def _canon_broker(v: Optional[str]) -> Optional[str]:
    if not v:
        return None
    key = str(v).strip().upper()
    if key in _CANON:
        return _CANON[key]
    if "RAKUTEN" in key:
        return "楽天"
    if "MATSUI" in key:
        return "松井"
    if "SBI" in key:
        return "SBI"
    vjp = str(v).strip().replace("証券", "")
    return _CANON.get(vjp, vjp or None)

def _int_amount(x) -> int:
    try:
        return int(round(float(x)))
    except Exception:
        return 0

def _net_amount_of_div(d: Dividend) -> int:
    try:
        return _int_amount(d.net_amount())
    except Exception:
        amt = float(d.amount or 0)
        if getattr(d, "is_net", True):
            return _int_amount(amt)
        tax = float(getattr(d, "tax", 0) or 0)
        return _int_amount(max(0.0, amt - tax))

def _realized_cashflow(x: RealizedTrade) -> int:
    try:
        return _int_amount(x.cashflow_effective)
    except Exception:
        signed = float(x.qty or 0) * float(x.price or 0)
        if (x.side or "").upper() == "BUY":
            signed = -signed
        fee = float(getattr(x, "fee", 0) or 0)
        tax = float(getattr(x, "tax", 0) or 0)
        return _int_amount(signed - fee - tax)

def _find_account(broker_like: str, currency: str = "JPY") -> Optional[BrokerAccount]:
    """
    口座検索は段階的に：
      1) broker=＜楽天/松井/SBI＞ かつ account_type='現物'
      2) broker=＜…＞ の任意口座（最初の1件）
      3) ensure_default_accounts() 後に 1)→2) を再試行
    """
    b = _canon_broker(broker_like)
    if not b:
        return None

    acc = (
        BrokerAccount.objects.filter(broker=b, account_type="現物", currency=currency)
        .order_by("id")
        .first()
    )
    if acc:
        return acc

    acc = (
        BrokerAccount.objects.filter(broker=b, currency=currency)
        .order_by("id")
        .first()
    )
    if acc:
        return acc

    svc.ensure_default_accounts(currency=currency)

    acc = (
        BrokerAccount.objects.filter(broker=b, account_type="現物", currency=currency)
        .order_by("id")
        .first()
    ) or (
        BrokerAccount.objects.filter(broker=b, currency=currency)
        .order_by("id")
        .first()
    )
    return acc

def _upsert_ledger(
    *,
    source_type: CashLedger.SourceType,
    source_id: int,
    account: BrokerAccount,
    at,  # date
    amount: int,
    memo: str,
) -> str:
    """
    既存があれば更新・なければ作成。戻り値: 'created' | 'updated' | 'skipped'
    """
    row = CashLedger.objects.filter(source_type=source_type, source_id=source_id).first()
    if not row:
        CashLedger.objects.create(
            account=account,
            at=at,
            amount=amount,
            kind=CashLedger.Kind.SYSTEM,
            memo=memo,
            source_type=source_type,
            source_id=source_id,
        )
        return "created"

    changed = False
    if row.at != at:
        row.at = at
        changed = True
    if _int_amount(row.amount) != _int_amount(amount):
        row.amount = amount
        changed = True
    if row.account_id != account.id:
        row.account = account
        changed = True
    nm = (memo or "").strip()
    if (row.memo or "").strip() != nm:
        row.memo = nm
        changed = True

    if changed:
        row.save(update_fields=["account", "at", "amount", "memo"])
        return "updated"
    return "skipped"

@transaction.atomic
def _sync_dividend(d: Dividend) -> Optional[str]:
    """配当 1件 → Ledger（税引後・支払日）を upsert"""
    broker = _canon_broker(getattr(d, "broker", None)) or _canon_broker(
        getattr(getattr(d, "holding", None), "broker", None)
    )
    acc = _find_account(broker or "")
    if not acc:
        return None

    amount = _net_amount_of_div(d)
    if amount == 0:
        return None

    memo = f"配当 {(d.display_ticker or d.ticker or '').upper()}".strip()
    return _upsert_ledger(
        source_type=CashLedger.SourceType.DIVIDEND,
        source_id=d.id,
        account=acc,
        at=d.date,  # 支払日
        amount=amount,
        memo=memo,
    )

@transaction.atomic
def _sync_realized(x: RealizedTrade) -> Optional[str]:
    """実損（特定/NISAのみ）→ Ledger（受渡金額・取引日）を upsert"""
    if (x.account or "").upper() not in ("SPEC", "NISA"):
        return None

    broker = _canon_broker(getattr(x, "broker", None))
    acc = _find_account(broker or "")
    if not acc:
        return None

    amount = _realized_cashflow(x)
    if amount == 0:
        return None

    memo = f"実現損益 {(x.ticker or '').upper()}".strip()
    return _upsert_ledger(
        source_type=CashLedger.SourceType.REALIZED,
        source_id=x.id,
        account=acc,
        at=x.trade_at,  # 取引日
        amount=amount,
        memo=memo,
    )

def sync_all() -> Dict[str, Any]:
    """
    ダッシュボード／台帳から毎回呼ぶ同期。
      - 配当 … 税引後額・支払日で upsert
      - 実損 … 受渡金額・取引日で upsert（特定/NISAのみ）
    何度呼んでも二重登録されない（idempotent）。
    """
    created_div = updated_div = 0
    created_real = updated_real = 0

    for d in Dividend.objects.all().only(
        "id", "date", "ticker", "broker", "amount", "is_net", "tax", "holding"
    ):
        try:
            res = _sync_dividend(d)
            if res == "created":
                created_div += 1
            elif res == "updated":
                updated_div += 1
        except Exception:
            continue

    for x in RealizedTrade.objects.filter(account__in=["SPEC", "NISA"]).only(
        "id", "trade_at", "ticker", "broker", "account", "cashflow",
        "side", "qty", "price", "fee", "tax"
    ):
        try:
            res = _sync_realized(x)
            if res == "created":
                created_real += 1
            elif res == "updated":
                updated_real += 1
        except Exception:
            continue

    return {
        "dividends_created": created_div,
        "dividends_updated": updated_div,
        "realized_created": created_real,
        "realized_updated": updated_real,
    }