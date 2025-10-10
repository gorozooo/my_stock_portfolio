# portfolio/services/cash_updater.py
# -*- coding: utf-8 -*-
from __future__ import annotations
from typing import Dict, Any, Literal

from django.db import transaction

from ..models import Dividend, RealizedTrade
from ..models_cash import BrokerAccount, CashLedger
from . import cash_service as svc

# cash_service にあるマッピングを利用（なければフォールバック）
try:
    from .cash_service import BROKER_CODE_TO_JA
except Exception:
    BROKER_CODE_TO_JA = {"RAKUTEN": "楽天", "MATSUI": "松井", "SBI": "SBI"}


def _find_account(broker_ja: str, currency: str = "JPY") -> BrokerAccount | None:
    """
    日本語ブローカー名（例: 楽天/松井/SBI）の代表「現物」口座を返す。
    なければ ensure_default_accounts() で作られる想定。
    """
    svc.ensure_default_accounts(currency=currency)
    return (
        BrokerAccount.objects.filter(broker=broker_ja, account_type="現物", currency=currency)
        .order_by("id")
        .first()
    )


def _int_amount(x) -> int:
    try:
        return int(round(float(x)))
    except Exception:
        return 0


def _upsert_legacy_row_cleanup(kind_prefix: str) -> None:
    """
    旧式（source_type なし かつ メモが「配当/実現損益」）の行が二重計上の原因になる。
    ただし削除は慎重に。ここでは削除は行わない（ビュー側で除外しているため）。
    将来、必要に応じて安全な条件で削除ロジックを追加。
    """
    return


@transaction.atomic
def _upsert_ledger_for_dividend(d: Dividend) -> Literal["created", "updated", "skipped"]:
    """
    配当 → CashLedger を upsert
      - 金額：net（税引後）
      - 日付：支払日（d.date）
      - 口座：該当ブローカーの現物口座
      - kind：SYSTEM（自動計上）
      - source: (DIVIDEND, d.id)
    """
    broker_ja = BROKER_CODE_TO_JA.get(getattr(d, "broker", ""), None)
    if not broker_ja:
        return "skipped"

    acc = _find_account(broker_ja)
    if not acc:
        return "skipped"

    amount = _int_amount(d.net_amount())
    if amount == 0:
        return "skipped"

    memo = f"配当 {d.display_ticker or d.ticker or ''}".strip()

    row = CashLedger.objects.filter(
        source_type=CashLedger.SourceType.DIVIDEND, source_id=d.id
    ).first()

    if row is None:
        CashLedger.objects.create(
            account=acc,
            at=d.date,
            amount=amount,
            kind=CashLedger.Kind.SYSTEM,
            memo=memo,
            source_type=CashLedger.SourceType.DIVIDEND,
            source_id=d.id,
        )
        _upsert_legacy_row_cleanup("配当")
        return "created"

    # 既存行を“正”に寄せる（上書き更新）
    changed = False
    if row.account_id != acc.id:
        row.account = acc
        changed = True
    if row.at != d.date:
        row.at = d.date
        changed = True
    if _int_amount(row.amount) != amount:
        row.amount = amount
        changed = True
    if (row.memo or "") != memo:
        row.memo = memo
        changed = True
    if row.kind != CashLedger.Kind.SYSTEM:
        row.kind = CashLedger.Kind.SYSTEM
        changed = True

    if changed:
        row.save(update_fields=["account", "at", "amount", "memo", "kind"])
        _upsert_legacy_row_cleanup("配当")
        return "updated"

    return "skipped"


@transaction.atomic
def _upsert_ledger_for_realized(x: RealizedTrade) -> Literal["created", "updated", "skipped"]:
    """
    実損 → CashLedger を upsert（現物のみ：特定/NISA）
      - 金額：cashflow_effective（SELL=＋、BUY=−、手数料/税込み）
      - 日付：取引日（x.trade_at）
      - 口座：該当ブローカーの現物口座
      - kind：SYSTEM（自動計上）
      - source: (REALIZED, x.id)
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

    memo = f"実現損益 {x.ticker}".strip()

    row = CashLedger.objects.filter(
        source_type=CashLedger.SourceType.REALIZED, source_id=x.id
    ).first()

    if row is None:
        CashLedger.objects.create(
            account=acc,
            at=x.trade_at,
            amount=amount,
            kind=CashLedger.Kind.SYSTEM,
            memo=memo,
            source_type=CashLedger.SourceType.REALIZED,
            source_id=x.id,
        )
        _upsert_legacy_row_cleanup("実現損益")
        return "created"

    # 既存行の上書き更新
    changed = False
    if row.account_id != acc.id:
        row.account = acc
        changed = True
    if row.at != x.trade_at:
        row.at = x.trade_at
        changed = True
    if _int_amount(row.amount) != amount:
        row.amount = amount
        changed = True
    if (row.memo or "") != memo:
        row.memo = memo
        changed = True
    if row.kind != CashLedger.Kind.SYSTEM:
        row.kind = CashLedger.Kind.SYSTEM
        changed = True

    if changed:
        row.save(update_fields=["account", "at", "amount", "memo", "kind"])
        _upsert_legacy_row_cleanup("実現損益")
        return "updated"

    return "skipped"


def sync_all() -> Dict[str, Any]:
    """
    ダッシュボード／台帳で呼ばれる同期本体（冪等）。
    - 配当：支払日で upsert
    - 実損：取引日で upsert（特定/NISAのみ）
    戻り値：新規/更新件数のサマリ
    """
    created_div = 0
    updated_div = 0
    created_real = 0
    updated_real = 0

    # ---- 配当 ----
    for d in Dividend.objects.all().only("id", "date", "ticker", "broker", "amount", "is_net"):
        try:
            res = _upsert_ledger_for_dividend(d)
            if res == "created":
                created_div += 1
            elif res == "updated":
                updated_div += 1
        except Exception:
            continue

    # ---- 実損（現物のみ）----
    for x in RealizedTrade.objects.all().only("id", "trade_at", "ticker", "broker", "account", "cashflow"):
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