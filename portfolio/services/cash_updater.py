# -*- coding: utf-8 -*-
from __future__ import annotations
from django.db import transaction
from django.db.models import Q

from portfolio.models import Dividend, RealizedTrade, Holding
from ..models_cash import BrokerAccount, CashLedger
from . import cash_service as svc


# =============================
# Broker 正規化
# =============================
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
    return qs.filter(Q(broker=code) | Q(broker=label)).first()


def _as_int(x) -> int:
    try:
        return int(round(float(x or 0)))
    except Exception:
        return 0


# =============================
# 内部ヘルパ
# =============================
def _upsert_ledger(**kwargs):
    """
    CashLedger を source_type + source_id で上書き（存在すれば更新）。
    """
    obj, created = CashLedger.objects.update_or_create(
        source_type=kwargs["source_type"],
        source_id=kwargs["source_id"],
        defaults=kwargs,
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

        # 対応する保有を探す（銘柄コード一致で1件目）
        holding = None
        if hasattr(d, "holding") and d.holding:
            holding = d.holding
        else:
            holding = Holding.objects.filter(ticker=d.ticker).first()

        created_flag = _upsert_ledger(
            account=acc,
            amount=amount,
            kind=CashLedger.Kind.DEPOSIT,
            memo=f"配当 {d.display_ticker or d.ticker or ''}",
            source_type=CashLedger.SourceType.DIVIDEND,
            source_id=d.id,
            holding=holding,
            at=d.created_at.date(),  # ← 登録日ベース
        )
        if created_flag:
            created += 1
        else:
            updated += 1
    return {"created": created, "updated": updated}


def sync_from_realized() -> dict:
    created = 0
    updated = 0
    for r in RealizedTrade.objects.all():
        acc = _get_account(r.broker)
        if not acc:
            continue
        delta = _as_int(r.cashflow_effective)
        if delta == 0:
            continue

        kind = CashLedger.Kind.DEPOSIT if delta > 0 else CashLedger.Kind.WITHDRAW

        holding = None
        try:
            holding = Holding.objects.filter(ticker=r.ticker).first()
        except Exception:
            pass

        created_flag = _upsert_ledger(
            account=acc,
            amount=delta,
            kind=kind,
            memo=f"実現損益 {r.ticker}",
            source_type=CashLedger.SourceType.REALIZED,
            source_id=r.id,
            holding=holding,
            at=r.created_at.date(),  # ← 登録日ベース
        )
        if created_flag:
            created += 1
        else:
            updated += 1
    return {"created": created, "updated": updated}


# =============================
# 統合呼び出し
# =============================
def sync_all() -> dict:
    """
    全ソース（配当・実現損益）を同期。
    - 登録日基準
    - 同じ source_type+source_id は上書き
    - holding を紐付け
    """
    svc.ensure_default_accounts()

    res_div = sync_from_dividends()
    res_real = sync_from_realized()

    return {
        "dividends_created": res_div["created"],
        "dividends_updated": res_div["updated"],
        "realized_created": res_real["created"],
        "realized_updated": res_real["updated"],
    }