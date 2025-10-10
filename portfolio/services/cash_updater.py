# portfolio/services/cash_updater.py
# -*- coding: utf-8 -*-
from __future__ import annotations
from typing import Dict, Any, Optional

from django.db import transaction

from ..models import Dividend, RealizedTrade, Holding
from ..models_cash import BrokerAccount, CashLedger
from . import cash_service as svc

# cash_service のマッピング（無ければフォールバック）
try:
    from .cash_service import BROKER_CODE_TO_JA
except Exception:
    BROKER_CODE_TO_JA = {"RAKUTEN": "楽天", "MATSUI": "松井", "SBI": "SBI"}

CODE2JA = BROKER_CODE_TO_JA
JA2CODE = {v: k for k, v in CODE2JA.items()}

# ---------- helpers ---------------------------------------------------------

def _ja_broker_from_code_or_ja(value: str | None) -> Optional[str]:
    """ 'RAKUTEN' → '楽天'、既に日本語ならそのまま、空/OTHERなら None """
    s = (value or "").strip()
    if not s:
        return None
    if s in CODE2JA:
        return CODE2JA[s]
    if s in JA2CODE:  # 既に日本語（楽天/松井/SBI）
        return s
    if s == "OTHER":
        return None
    return None

def _find_account(broker_ja: str, currency: str = "JPY") -> Optional[BrokerAccount]:
    """ 指定ブローカーの '現物' 口座 """
    svc.ensure_default_accounts(currency=currency)
    return (
        BrokerAccount.objects
        .filter(broker=broker_ja, account_type="現物", currency=currency)
        .order_by("id").first()
    )

def _fallback_any_account() -> Optional[BrokerAccount]:
    """最終保険：どれかの現物口座（楽天→松井→SBI の優先）"""
    svc.ensure_default_accounts()
    for b in ("楽天", "松井", "SBI"):
        acc = _find_account(b)
        if acc:
            return acc
    return BrokerAccount.objects.filter(account_type="現物").order_by("id").first()

def _int_amount(x) -> int:
    try:
        return int(round(float(x)))
    except Exception:
        return 0

def _get_existing_ledger(source_type: CashLedger.SourceType, source_id: int) -> Optional[CashLedger]:
    return CashLedger.objects.filter(source_type=source_type, source_id=source_id).first()

def _guess_broker_from_holding(ticker: str) -> Optional[str]:
    """同じティッカーの Holding から broker を推定（SPEC/NISA を優先）"""
    t = (ticker or "").strip().upper()
    if not t:
        return None
    qs = (Holding.objects.filter(ticker=t)
          .order_by()  # 明示 reset
          .only("broker", "account"))
    # SPEC/NISA を優先して探す
    pref = qs.filter(account__in=("SPEC", "NISA")).first() or qs.first()
    if not pref:
        return None
    return _ja_broker_from_code_or_ja(pref.broker)

def _guess_broker_from_existing_ledgers() -> Optional[str]:
    """既存 Ledger から多いブローカーを採用（初期導入の保険）"""
    row = (CashLedger.objects
           .values_list("account__broker", flat=True)
           .order_by()
           .first())
    return (row or None)

# ---------- upsert: Dividend ------------------------------------------------

@transaction.atomic
def _upsert_ledger_for_dividend(d: Dividend) -> str:
    """
    配当 → CashLedger 作成/更新
    - 金額: 税引後(net)
    - 日付: 支払日(d.date)
    - 口座: broker が無ければ Holding/既存 Ledger から推定
    """
    # 1) broker 値から日本語ブローカー名へ
    broker_ja = _ja_broker_from_code_or_ja(getattr(d, "broker", ""))

    # 2) 無ければ Holding から推定
    if not broker_ja:
        broker_ja = _guess_broker_from_holding(getattr(d, "ticker", "")) or broker_ja

    # 3) まだ無ければ 既存 Ledger から推定
    if not broker_ja:
        broker_ja = _guess_broker_from_existing_ledgers() or broker_ja

    # 4) 最後の保険：どれかの現物口座
    acc = _find_account(broker_ja) if broker_ja else None
    if not acc:
        acc = _fallback_any_account()
    if not acc:
        return "skipped"

    amount = _int_amount(d.net_amount())
    if amount == 0:
        return "skipped"

    at_val = d.date
    memo   = f"配当 { (d.display_ticker or d.ticker or '').strip() }".strip()

    existing = _get_existing_ledger(CashLedger.SourceType.DIVIDEND, d.id)
    if existing is None:
        CashLedger.objects.create(
            account=acc,
            at=at_val,
            amount=amount,
            kind=CashLedger.Kind.SYSTEM,
            memo=memo,
            source_type=CashLedger.SourceType.DIVIDEND,
            source_id=d.id,
        )
        return "created"

    changed = False
    if existing.account_id != acc.id:
        existing.account = acc; changed = True
    if existing.at != at_val:
        existing.at = at_val; changed = True
    if int(existing.amount) != int(amount):
        existing.amount = amount; changed = True
    if (existing.memo or "") != memo:
        existing.memo = memo; changed = True
    if changed:
        existing.kind = CashLedger.Kind.SYSTEM
        existing.save(update_fields=["account", "at", "amount", "memo", "kind"])
        return "updated"
    return "skipped"


# ---------- upsert: RealizedTrade ------------------------------------------

@transaction.atomic
def _upsert_ledger_for_realized(x: RealizedTrade) -> str:
    """
    実損（現物のみ）→ CashLedger 作成/更新
    - 対象: SPEC/NISA
    - 金額: cashflow_effective
    - 日付: trade_at
    - broker 不明時は Holding/既存 Ledger から推定
    """
    if getattr(x, "account", "") not in ("SPEC", "NISA"):
        return "skipped"

    broker_ja = _ja_broker_from_code_or_ja(getattr(x, "broker", "")) \
                or _guess_broker_from_holding(getattr(x, "ticker", "")) \
                or _guess_broker_from_existing_ledgers()

    acc = _find_account(broker_ja) if broker_ja else None
    if not acc:
        acc = _fallback_any_account()
    if not acc:
        return "skipped"

    amount = _int_amount(x.cashflow_effective)
    if amount == 0:
        return "skipped"

    at_val = x.trade_at
    memo   = f"実現損益 { (x.ticker or '').strip().upper() }"

    existing = _get_existing_ledger(CashLedger.SourceType.REALIZED, x.id)
    if existing is None:
        CashLedger.objects.create(
            account=acc,
            at=at_val,
            amount=amount,
            kind=CashLedger.Kind.SYSTEM,
            memo=memo,
            source_type=CashLedger.SourceType.REALIZED,
            source_id=x.id,
        )
        return "created"

    changed = False
    if existing.account_id != acc.id:
        existing.account = acc; changed = True
    if existing.at != at_val:
        existing.at = at_val; changed = True
    if int(existing.amount) != int(amount):
        existing.amount = amount; changed = True
    if (existing.memo or "") != memo:
        existing.memo = memo; changed = True
    if changed:
        existing.kind = CashLedger.Kind.SYSTEM
        existing.save(update_fields=["account", "at", "amount", "memo", "kind"])
        return "updated"
    return "skipped"


# ---------- public: sync_all -----------------------------------------------

def sync_all() -> Dict[str, Any]:
    """
    ダッシュボード/台帳で呼ばれる idempotent 同期。
      - 配当: 支払日で upsert
      - 実損: 取引日で upsert（SPEC/NISA）
      - broker 未設定でも推定して極力反映
    """
    created_div = updated_div = 0
    created_real = updated_real = 0

    for d in Dividend.objects.all().only(
        "id", "date", "ticker", "name", "broker", "amount", "tax", "is_net"
    ):
        try:
            res = _upsert_ledger_for_dividend(d)
            if res == "created": created_div += 1
            elif res == "updated": updated_div += 1
        except Exception:
            continue

    for x in RealizedTrade.objects.all().only(
        "id", "trade_at", "ticker", "broker", "account", "cashflow", "fee", "tax", "side", "qty", "price"
    ):
        try:
            res = _upsert_ledger_for_realized(x)
            if res == "created": created_real += 1
            elif res == "updated": updated_real += 1
        except Exception:
            continue

    return {
        "dividends_created": created_div,
        "dividends_updated": updated_div,
        "realized_created": created_real,
        "realized_updated": updated_real,
    }