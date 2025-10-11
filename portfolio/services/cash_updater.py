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
    """BrokerAccount は“コード or 日本語名”のどちらでもヒットさせる"""
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
# Holding 検索（希望口座優先）
# =============================
def _find_holding(broker: str, ticker: str, desired_account: str | None = None) -> Holding | None:
    """
    優先順位：
      ① broker一致 + ticker一致 + account == desired_account（指定があれば）
      ② broker一致 + ticker一致 + account in (SPEC, NISA) ← 現物優先
      ③ broker一致 + ticker一致
      ④ ticker一致
    """
    if not ticker:
        return None

    code = _norm_broker(broker)
    ja   = _label_from_code(code)

    base = Holding.objects.filter(ticker=ticker)

    # ① 希望アカウント完全一致
    if desired_account:
        qs0 = base.filter(
            Q(broker__in=[code, ja]),
            Q(account=desired_account)
        ).order_by("-updated_at", "-id")
        if qs0.exists():
            return qs0.first()

    # ② 現物（特定/NISA）優先
    qs1 = base.filter(
        Q(broker__in=[code, ja]),
        Q(account__in=["SPEC", "NISA"]),
    ).order_by("-updated_at", "-id")
    if qs1.exists():
        return qs1.first()

    # ③ broker一致
    qs2 = base.filter(broker__in=[code, ja]).order_by("-updated_at", "-id")
    if qs2.exists():
        return qs2.first()

    # ④ ticker一致のみ
    qs3 = base.order_by("-updated_at", "-id")
    return qs3.first() if qs3.exists() else None


# =============================
# Upsert（source_type + source_id で一意）
# =============================
def _upsert_ledger(**defaults) -> bool:
    obj, created = CashLedger.objects.update_or_create(
        source_type=defaults["source_type"],
        source_id=defaults["source_id"],
        defaults=defaults,
    )
    return created


# =============================
# 同期ロジック
# =============================
def sync_from_dividends() -> dict:
    """
    配当：
      - 日付: d.date（支払日）
      - 金額: 税引後（net）
      - 種別: DEPOSIT（入金）
      - Holding: d.holding が無ければ broker/ticker/account から探索
    """
    created = 0
    updated = 0

    for d in Dividend.objects.all():
        acc = _get_account(d.broker)
        if not acc:
            continue

        amount = _as_int(d.net_amount())
        if amount <= 0:
            continue

        # holding 優先：明示が無ければ探索（配当の口座区分を優先ヒントに）
        holding = getattr(d, "holding", None) or _find_holding(d.broker, d.ticker, desired_account=getattr(d, "account", None))

        created_now = _upsert_ledger(
            account=acc,
            at=d.date,  # 支払日
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
    実損（売買・受渡）：
      - 対象: **すべて**（SPEC/NISA/MARGIN を除外しない）
      - 日付: r.trade_at（取引日）
      - 金額: r.cashflow_effective（SELL=＋ / BUY=− / 手数料・税込み）
      - 種別: 正なら DEPOSIT, 負なら WITHDRAW
      - Holding: broker/ticker/口座でできるだけ一致を探す
    """
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

        # できるだけ同じ口座区分を優先して紐付け
        desired = getattr(r, "account", None)
        holding = _find_holding(r.broker, r.ticker, desired_account=desired)

        created_now = _upsert_ledger(
            account=acc,
            at=r.trade_at,  # 取引日（登録日ではない）
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
# 統合エントリ
# =============================
@transaction.atomic
def sync_all() -> dict:
    """
    - 配当：支払日で Ledger 作成/更新、holding を可能な限り紐付け
    - 実損：取引日で Ledger 作成/更新、**SPEC/NISA/MARGIN すべて対象**
    - source_type + source_id で完全 upsert（重複しない、毎回上書き）
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