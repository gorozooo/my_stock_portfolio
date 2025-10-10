# portfolio/services/cash_updater.py
# -*- coding: utf-8 -*-
from __future__ import annotations
from typing import Dict, Any, Optional

from django.db import transaction

from ..models import Dividend, RealizedTrade
from ..models_cash import BrokerAccount, CashLedger
from . import cash_service as svc

# cash_service にあるマッピングを利用（なければフォールバック）
try:
    from .cash_service import BROKER_CODE_TO_JA
except Exception:
    BROKER_CODE_TO_JA = {"RAKUTEN": "楽天", "MATSUI": "松井", "SBI": "SBI"}


# ---------- helpers ---------------------------------------------------------

def _find_account(broker_ja: str, currency: str = "JPY") -> Optional[BrokerAccount]:
    """
    日本語ブローカー名（例: 楽天/松井/SBI）の代表「現物」口座を返す。
    無ければ ensure_default_accounts() で自動生成される想定。
    """
    svc.ensure_default_accounts(currency=currency)
    return (
        BrokerAccount.objects
        .filter(broker=broker_ja, account_type="現物", currency=currency)
        .order_by("id")
        .first()
    )


def _int_amount(x) -> int:
    try:
        return int(round(float(x)))
    except Exception:
        return 0


def _get_existing_ledger(source_type: CashLedger.SourceType, source_id: int) -> Optional[CashLedger]:
    return CashLedger.objects.filter(source_type=source_type, source_id=source_id).first()


# ---------- upsert: Dividend ------------------------------------------------

@transaction.atomic
def _upsert_ledger_for_dividend(d: Dividend) -> str:
    """
    配当1件 → CashLedger を作成 or 上書き更新
      - 金額: 税引後(net) 推奨
      - 日付: 支払日(d.date)
      - source: (DIVIDEND, d.id)
    戻り値: "created" | "updated" | "skipped"
    """
    broker_ja = BROKER_CODE_TO_JA.get(getattr(d, "broker", ""), None)
    # broker コードが OTHER 等で map できない場合は安全側でスキップ
    if not broker_ja:
        return "skipped"

    acc = _find_account(broker_ja)
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
            kind=CashLedger.Kind.SYSTEM,  # 自動計上
            memo=memo,
            source_type=CashLedger.SourceType.DIVIDEND,
            source_id=d.id,
        )
        return "created"

    # 差分があれば更新（at/amount/memo/account）
    changed = False
    if existing.account_id != acc.id:
        existing.account = acc
        changed = True
    if existing.at != at_val:
        existing.at = at_val
        changed = True
    if int(existing.amount) != int(amount):
        existing.amount = amount
        changed = True
    if (existing.memo or "") != memo:
        existing.memo = memo
        changed = True

    if changed:
        existing.kind = CashLedger.Kind.SYSTEM  # 念のため
        existing.save(update_fields=["account", "at", "amount", "memo", "kind"])
        return "updated"

    return "skipped"


# ---------- upsert: RealizedTrade ------------------------------------------

@transaction.atomic
def _upsert_ledger_for_realized(x: RealizedTrade) -> str:
    """
    実損（受渡金額）→ CashLedger を作成 or 上書き更新
      - 対象: 現物系のみ（特定 SPEC / NISA）。信用は除外。
      - 金額: x.cashflow_effective（SELL=＋ / BUY=− / 手数料・税控除後）
      - 日付: 取引日（x.trade_at）
      - source: (REALIZED, x.id)
    戻り値: "created" | "updated" | "skipped"
    """
    if getattr(x, "account", "") not in ("SPEC", "NISA"):
        return "skipped"

    broker_ja = BROKER_CODE_TO_JA.get(getattr(x, "broker", ""), None)
    if not broker_ja:
        return "skipped"

    acc = _find_account(broker_ja)
    if not acc:
        return "skipped"

    amount = _int_amount(x.cashflow_effective)
    if amount == 0:
        return "skipped"

    at_val = x.trade_at
    memo   = f"実現損益 { (x.ticker or '').strip().upper() }".strip()

    existing = _get_existing_ledger(CashLedger.SourceType.REALIZED, x.id)
    if existing is None:
        CashLedger.objects.create(
            account=acc,
            at=at_val,
            amount=amount,
            kind=CashLedger.Kind.SYSTEM,  # 自動計上
            memo=memo,
            source_type=CashLedger.SourceType.REALIZED,
            source_id=x.id,
        )
        return "created"

    # 差分があれば更新（at/amount/memo/account）
    changed = False
    if existing.account_id != acc.id:
        existing.account = acc
        changed = True
    if existing.at != at_val:
        existing.at = at_val
        changed = True
    if int(existing.amount) != int(amount):
        existing.amount = amount
        changed = True
    if (existing.memo or "") != memo:
        existing.memo = memo
        changed = True

    if changed:
        existing.kind = CashLedger.Kind.SYSTEM
        existing.save(update_fields=["account", "at", "amount", "memo", "kind"])
        return "updated"

    return "skipped"


# ---------- public: sync_all -----------------------------------------------

def sync_all() -> Dict[str, Any]:
    """
    ダッシュボード／台帳で呼ばれる同期本体（idempotent）。
      - 配当  : 未作成は作成、差分があれば上書き（支払日で反映）
      - 実損  : 未作成は作成、差分があれば上書き（現物のみ / 取引日で反映）
    戻り値: 作成/更新件数のサマリ
    """
    created_div = updated_div = 0
    created_real = updated_real = 0

    # ---- 配当 ----
    for d in Dividend.objects.all().only(
        "id", "date", "ticker", "name", "broker", "amount", "tax", "is_net"
    ):
        try:
            res = _upsert_ledger_for_dividend(d)
            if res == "created":
                created_div += 1
            elif res == "updated":
                updated_div += 1
        except Exception:
            # 1 レコード失敗しても他は継続
            continue

    # ---- 実損（現物のみ）----
    for x in RealizedTrade.objects.all().only(
        "id", "trade_at", "ticker", "broker", "account", "cashflow", "fee", "tax", "side", "qty", "price"
    ):
        try:
            res = _upsert_ledger_for_realized(x)
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