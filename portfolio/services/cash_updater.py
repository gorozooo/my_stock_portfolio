# portfolio/services/cash_updater.py
# -*- coding: utf-8 -*-
from __future__ import annotations

from django.db import transaction
from django.db.models import Q

from ..models import Dividend, RealizedTrade, Holding
from ..models_cash import BrokerAccount, CashLedger
from . import cash_service as svc


# =============================
# Broker 正規化 & 口座解決
# =============================
def _norm_broker(code: str) -> str:
    if not code:
        return ""
    s = str(code).strip().upper()
    if "RAKUTEN" in s or "楽天" in s:
        return "RAKUTEN"
    if "MATSUI" in s or "松井" in s:
        return "MATSUI"
    if "SBI" in s:
        return "SBI"
    return "OTHER"


def _label_from_code(code: str) -> str:
    return {"RAKUTEN": "楽天", "MATSUI": "松井", "SBI": "SBI", "OTHER": "その他"}.get(code, code)


def _get_account(broker_code: str, currency: str = "JPY") -> BrokerAccount | None:
    """
    BrokerAccount.broker が「楽天/松井/SBI」（日本語名）でも
    「RAKUTEN/MATSUI/SBI」（英字コード）でもヒットするよう両方で検索。
    """
    svc.ensure_default_accounts(currency=currency)
    code = _norm_broker(broker_code)
    label = _label_from_code(code)
    return (
        BrokerAccount.objects.filter(currency=currency)
        .filter(Q(broker=code) | Q(broker=label))
        .order_by("id")
        .first()
    )


def _as_int(x) -> int:
    try:
        return int(round(float(x or 0)))
    except Exception:
        return 0


# =============================
# 内部ヘルパ
# =============================
def _upsert_ledger(**defaults) -> bool:
    """
    CashLedger を (source_type, source_id) で upsert。
    既存があれば更新、なければ作成。作成時 True / 更新時 False を返す。
    """
    obj, created = CashLedger.objects.update_or_create(
        source_type=defaults["source_type"],
        source_id=defaults["source_id"],
        defaults=defaults,
    )
    return created


def _find_holding_by_ticker(ticker: str) -> Holding | None:
    t = (ticker or "").strip().upper()
    if not t:
        return None
    return Holding.objects.filter(ticker=t).order_by("-updated_at", "-id").first()


# =============================
# 同期ロジック（配当・実損）
# =============================
def sync_from_dividends() -> dict:
    """
    配当 → 受取（DEPOSIT）1行。
    日付は **支払日（Dividend.date）** を使用。
    holding は Dividend.holding があればそれ、なければ ticker で推定。
    """
    created = 0
    updated = 0

    for d in Dividend.objects.all():
        acc = _get_account(getattr(d, "broker", ""))
        if not acc:
            continue

        amount = _as_int(d.net_amount())  # UI は税引後前提
        if amount <= 0:
            continue

        holding = getattr(d, "holding", None) or _find_holding_by_ticker(d.ticker)

        created_now = _upsert_ledger(
            account=acc,
            at=d.date,  # ← 支払日
            amount=amount,
            kind=CashLedger.Kind.DEPOSIT,
            memo=f"配当 {d.display_ticker or d.ticker or ''}".strip(),
            source_type=CashLedger.SourceType.DIVIDEND,
            source_id=d.id,
            holding=holding,
        )
        if created_now:
            created += 1
        else:
            updated += 1

    return {"created": created, "updated": updated}


def sync_from_realized() -> dict:
    """
    実損 → 受渡（SELL=入金 / BUY=出金）1行。
    日付は **取引日（RealizedTrade.trade_at）** を使用。
    対象は **現物のみ（特定 / NISA）**。信用は除外。
    holding は ticker から推定（必要なら将来: 紐付けカラムで厳密化）。
    """
    created = 0
    updated = 0

    for r in RealizedTrade.objects.all():
        if getattr(r, "account", "") not in ("SPEC", "NISA"):
            continue

        acc = _get_account(getattr(r, "broker", ""))
        if not acc:
            continue

        delta = _as_int(r.cashflow_effective)  # SELL=＋ / BUY=−（手数料・税込み）
        if delta == 0:
            continue

        kind = CashLedger.Kind.DEPOSIT if delta > 0 else CashLedger.Kind.WITHDRAW
        holding = _find_holding_by_ticker(r.ticker)

        created_now = _upsert_ledger(
            account=acc,
            at=r.trade_at,  # ← 取引日
            amount=delta,
            kind=kind,
            memo=f"実現損益 {r.ticker}".strip(),
            source_type=CashLedger.SourceType.REALIZED,
            source_id=r.id,
            holding=holding,
        )
        if created_now:
            created += 1
        else:
            updated += 1

    return {"created": created, "updated": updated}


# =============================
# 統合呼び出し
# =============================
@transaction.atomic
def sync_all() -> dict:
    """
    - 支払日/取引日ベースで CashLedger を upsert（毎回上書き）
    - Dividend/RealizedTrade から Holding を可能な限り紐付け
    - 更新件数を返す（ビュー側のトーストにそのまま使える）
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