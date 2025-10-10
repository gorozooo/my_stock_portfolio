# portfolio/services/cash_updater.py
# -*- coding: utf-8 -*-
from __future__ import annotations
from typing import Dict, Any

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


def _exists_ledger(source_type: CashLedger.SourceType, source_id: int) -> bool:
    return CashLedger.objects.filter(source_type=source_type, source_id=source_id).exists()


def _int_amount(x) -> int:
    try:
        return int(round(float(x)))
    except Exception:
        return 0


@transaction.atomic
def _ensure_ledger_for_dividend(d: Dividend) -> bool:
    """
    配当1件 → CashLedger（受け取り）を 1 行作る（既にあれば作らない）
    - 金額：net（税引後）を推奨（UI が税引後前提のため）
    - 日付：発生日（d.date）
    - source: (DIVIDEND, d.id)
    """
    if _exists_ledger(CashLedger.SourceType.DIVIDEND, d.id):
        return False

    broker_ja = BROKER_CODE_TO_JA.get(getattr(d, "broker", ""), None)
    # broker コードが OTHER 等で map できない場合は自動作成をスキップ（安全側）
    if not broker_ja:
        return False

    acc = _find_account(broker_ja)
    if not acc:
        return False

    amount = _int_amount(d.net_amount())
    if amount == 0:
        # 0 は作っても意味が薄いのでスキップ（必要なら外してOK）
        return False

    CashLedger.objects.create(
        account=acc,
        at=d.date,                                     # ← 発生日で登録
        amount=amount,
        kind=CashLedger.Kind.SYSTEM,                   # 自動計上
        memo=f"配当 {d.display_ticker or d.ticker or ''}".strip(),
        source_type=CashLedger.SourceType.DIVIDEND,
        source_id=d.id,
    )
    return True


@transaction.atomic
def _ensure_ledger_for_realized(x: RealizedTrade) -> bool:
    """
    実損（受渡金額）→ CashLedger を 1 行作る（既にあれば作らない）
    対象：現物系のみ（特定 / NISA）, 信用は除外
    - 金額：x.cashflow_effective（SELL=＋ / BUY=− / 手数料・税考慮）
    - 日付：発生日（x.trade_at）
    - source: (REALIZED, x.id)
    """
    if _exists_ledger(CashLedger.SourceType.REALIZED, x.id):
        return False

    if getattr(x, "account", "") not in ("SPEC", "NISA"):
        return False

    broker_ja = BROKER_CODE_TO_JA.get(getattr(x, "broker", ""), None)
    if not broker_ja:
        return False

    acc = _find_account(broker_ja)
    if not acc:
        return False

    amount = _int_amount(x.cashflow_effective)
    if amount == 0:
        # 0 の受渡はスキップ（必要なら外す）
        return False

    CashLedger.objects.create(
        account=acc,
        at=x.trade_at,                                  # ← 取引発生日で登録
        amount=amount,
        kind=CashLedger.Kind.SYSTEM,                    # 自動計上
        memo=f"実現損益 {x.ticker}".strip(),
        source_type=CashLedger.SourceType.REALIZED,
        source_id=x.id,
    )
    return True


def sync_all() -> Dict[str, Any]:
    """
    ダッシュボード／台帳で呼ばれる同期本体。
    - 配当（全件）について、未作成の Ledger を『支払日』で作成
    - 実損（全件）について、未作成の Ledger を『取引日』で作成（特定/NISAのみ）
    何度呼んでも重複しない（idempotent）。
    戻り値は新規作成件数のサマリ。
    """
    created_div = 0
    created_real = 0

    # ---- 配当 ----
    for d in Dividend.objects.all().only("id", "date", "ticker", "broker", "amount", "is_net"):
        try:
            if _ensure_ledger_for_dividend(d):
                created_div += 1
        except Exception:
            # 1 レコード失敗しても他を継続
            continue

    # ---- 実損（現物のみ）----
    for x in RealizedTrade.objects.all().only("id", "trade_at", "ticker", "broker", "account", "cashflow"):
        try:
            if _ensure_ledger_for_realized(x):
                created_real += 1
        except Exception:
            continue

    return {
        "dividends_created": created_div,
        "realized_created": created_real,
    }