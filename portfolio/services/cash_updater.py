# portfolio/services/cash_updater.py
# -*- coding: utf-8 -*-
from __future__ import annotations
from typing import Dict, Any, Tuple

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


def _upsert_ledger(
    *,
    account: BrokerAccount,
    at,
    amount: int,
    memo: str,
    source_type: CashLedger.SourceType,
    source_id: int,
) -> Tuple[bool, bool]:
    """
    (created, updated) を返す upsert。
    一意キーは (source_type, source_id) だが、account が変わる可能性も考慮し上書きする。
    """
    created = False
    updated = False

    # 既存照会（account は問わずに source で紐づけ）
    row = CashLedger.objects.filter(source_type=source_type, source_id=source_id).first()

    if row is None:
        CashLedger.objects.create(
            account=account,
            at=at,
            amount=amount,
            kind=CashLedger.Kind.SYSTEM,
            memo=memo,
            source_type=source_type,
            source_id=source_id,
        )
        created = True
    else:
        # 差分があれば更新（account が違っても移し替える）
        need_update = (
            row.account_id != account.id
            or row.at != at
            or _int_amount(row.amount) != _int_amount(amount)
            or (row.memo or "") != (memo or "")
            or row.kind != CashLedger.Kind.SYSTEM
        )
        if need_update:
            row.account = account
            row.at = at
            row.amount = amount
            row.kind = CashLedger.Kind.SYSTEM
            row.memo = memo
            row.save(update_fields=["account", "at", "amount", "kind", "memo"])
            updated = True

    return created, updated


@transaction.atomic
def _upsert_for_dividend(d: Dividend) -> Tuple[bool, bool]:
    """
    配当 → CashLedger を upsert
    - 金額：net（税引後）
    - 日付：支払日 d.date
    - source: (DIVIDEND, d.id)
    """
    broker_code = getattr(d, "broker", "") or ""
    broker_ja = BROKER_CODE_TO_JA.get(broker_code)
    if not broker_ja:
        return False, False

    acc = _find_account(broker_ja)
    if not acc:
        return False, False

    amount = _int_amount(d.net_amount())
    if amount == 0:
        # 0 円はスキップ（必要なら削除）
        return False, False

    memo = f"配当 {(d.display_ticker or d.ticker or '').strip()}".strip()

    return _upsert_ledger(
        account=acc,
        at=d.date,
        amount=amount,
        memo=memo,
        source_type=CashLedger.SourceType.DIVIDEND,
        source_id=d.id,
    )


@transaction.atomic
def _upsert_for_realized(x: RealizedTrade) -> Tuple[bool, bool]:
    """
    実損（受渡金額） → CashLedger を upsert
    対象：現物系のみ（特定 / NISA）。信用は除外。
    - 金額：cashflow_effective（SELL=＋ / BUY=− / 手数料・税控除後）
    - 日付：取引日 x.trade_at
    - source: (REALIZED, x.id)
    """
    if getattr(x, "account", "") not in ("SPEC", "NISA"):
        return False, False

    broker_code = getattr(x, "broker", "") or ""
    broker_ja = BROKER_CODE_TO_JA.get(broker_code)
    if not broker_ja:
        return False, False

    acc = _find_account(broker_ja)
    if not acc:
        return False, False

    amount = _int_amount(x.cashflow_effective)
    if amount == 0:
        # 0 円はスキップ（必要なら削除）
        return False, False

    memo = f"実現損益 {(x.ticker or '').strip()}".strip()

    return _upsert_ledger(
        account=acc,
        at=x.trade_at,
        amount=amount,
        memo=memo,
        source_type=CashLedger.SourceType.REALIZED,
        source_id=x.id,
    )


def sync_all() -> Dict[str, Any]:
    """
    ダッシュボード／台帳で呼ばれる同期本体（冪等）。
    - 配当：全件を upsert（支払日で保存）
    - 実損：全件を upsert（取引日で保存, 特定/NISAのみ）
    何度呼んでも重複せず、変更があれば上書きされる。
    戻り値: 新規作成/更新件数サマリ
    """
    created_div = updated_div = 0
    created_real = updated_real = 0

    # --- 配当 ---
    # ※ .only(...) は net_amount() の内部参照（tax 等）を欠落させるので使わない
    for d in Dividend.objects.all():
        try:
            c, u = _upsert_for_dividend(d)
            created_div += 1 if c else 0
            updated_div += 1 if u else 0
        except Exception:
            # 1件失敗しても続行
            continue

    # --- 実損（現物のみ）---
    # cashflow_effective は @property のため .only(...) で削ると正しく計算できない
    for x in RealizedTrade.objects.all():
        try:
            c, u = _upsert_for_realized(x)
            created_real += 1 if c else 0
            updated_real += 1 if u else 0
        except Exception:
            continue

    return {
        "dividends_created": created_div,
        "dividends_updated": updated_div,
        "realized_created": created_real,
        "realized_updated": updated_real,
    }