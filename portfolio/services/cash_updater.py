# portfolio/services/cash_updater.py
# -*- coding: utf-8 -*-
from __future__ import annotations
from typing import Dict, Any, Tuple

from django.db import transaction

from ..models import Dividend, RealizedTrade
from ..models_cash import BrokerAccount, CashLedger
from . import cash_service as svc

# ====== Broker コード → 日本語名（BrokerAccount.broker 用） ======
# BrokerAccount 側は「楽天 / 松井 / SBI」の日本語で管理しているため、ここで変換する。
BROKER_CODE_TO_JA: Dict[str, str] = {
    "RAKUTEN": "楽天",
    "MATSUI":  "松井",
    "SBI":     "SBI",
    # 想定外は None 扱い（スキップ）
}

# ---------------------------------------------------------------------

def _find_spot_account(broker_code: str, currency: str = "JPY") -> BrokerAccount | None:
    """
    Realized/Dividend を計上する現金勘定（証券会社＝日本語、account_type=現物）を取得。
    なければ ensure_default_accounts() で作られている前提。
    """
    svc.ensure_default_accounts(currency=currency)

    broker_ja = BROKER_CODE_TO_JA.get((broker_code or "").upper())
    if not broker_ja:
        return None

    return (
        BrokerAccount.objects
        .filter(broker=broker_ja, account_type="現物", currency=currency)
        .order_by("id")
        .first()
    )


def _to_int(x) -> int:
    try:
        return int(round(float(x)))
    except Exception:
        return 0


# ====== UPSERT: Dividend =================================================
@transaction.atomic
def _upsert_ledger_for_dividend(d: Dividend) -> Tuple[bool, bool]:
    """
    配当 1件 → CashLedger を upsert（新規 or 更新）
    - 金額: 税引後（net_amount）
    - 日付: 支払日（d.date）
    - 帰属: source_type=DIVIDEND, source_id=d.id
    戻り値: (created, updated)
    """
    # broker マップ不可ならスキップ
    acc = _find_spot_account(d.broker)
    if not acc:
        return (False, False)

    amount = _to_int(d.net_amount())
    if amount == 0:
        return (False, False)

    defaults = dict(
        account=acc,
        at=d.date,
        amount=amount,
        kind=CashLedger.Kind.SYSTEM,  # 自動計上
        memo=f"配当 {(d.display_ticker or d.ticker or '').upper()}".strip() or "配当",
    )
    obj, created = CashLedger.objects.update_or_create(
        source_type=CashLedger.SourceType.DIVIDEND,
        source_id=d.id,
        defaults=defaults,
    )
    # update_or_create は既存でも fields を上書きするので、
    # 「新規かどうか」に加えて「更新が実質発生したか」を軽く判定
    updated = (not created)
    return (created, updated)


# ====== UPSERT: Realized (SPEC/NISA) ===================================
@transaction.atomic
def _upsert_ledger_for_realized(x: RealizedTrade) -> Tuple[bool, bool]:
    """
    実現損益（現物のみ: 特定/NISA）→ CashLedger を upsert
    - 金額: cashflow_effective（SELL=＋, BUY=−, 手数料/税込み）
    - 日付: 取引日（trade_at）
    - 帰属: source_type=REALIZED, source_id=x.id
    """
    if (x.account or "").upper() not in ("SPEC", "NISA"):
        return (False, False)

    acc = _find_spot_account(x.broker)
    if not acc:
        return (False, False)

    amount = _to_int(x.cashflow_effective)
    if amount == 0:
        return (False, False)

    defaults = dict(
        account=acc,
        at=x.trade_at,
        amount=amount,
        kind=CashLedger.Kind.SYSTEM,
        memo=f"実現損益 {(x.ticker or '').upper()}".strip() or "実現損益",
    )
    obj, created = CashLedger.objects.update_or_create(
        source_type=CashLedger.SourceType.REALIZED,
        source_id=x.id,
        defaults=defaults,
    )
    updated = (not created)
    return (created, updated)


# ====== PUBLIC: 全量同期 =================================================
def sync_all() -> Dict[str, Any]:
    """
    ダッシュボード / 台帳の表示前に呼ばれる同期本体。
    - Dividend 全件 → Ledger を『支払日』・税引後で upsert
    - RealizedTrade（SPEC/NISA）全件 → Ledger を『取引日』・受渡金額で upsert
    - 何度呼んでも重複しない（idempotent）。変更は上書きされる。
    戻り値: 新規件数 / 更新件数のサマリ
    """
    created_div = updated_div = 0
    created_real = updated_real = 0

    # ---- 配当 ----
    for d in Dividend.objects.all().only(
        "id", "date", "ticker", "name", "broker", "amount", "is_net", "tax"
    ):
        try:
            c, u = _upsert_ledger_for_dividend(d)
            created_div += int(c)
            updated_div += int(u)
        except Exception:
            # 1件失敗しても続行
            continue

    # ---- 実損（現物：特定/NISA のみ）----
    for x in RealizedTrade.objects.all().only(
        "id", "trade_at", "ticker", "broker", "account", "cashflow", "fee", "tax", "side", "qty", "price"
    ):
        try:
            c, u = _upsert_ledger_for_realized(x)
            created_real += int(c)
            updated_real += int(u)
        except Exception:
            continue

    return {
        "dividends_created": created_div,
        "dividends_updated": updated_div,
        "realized_created": created_real,
        "realized_updated": updated_real,
    }