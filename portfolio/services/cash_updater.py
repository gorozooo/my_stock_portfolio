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

    if desired_account:
        qs0 = base.filter(Q(broker__in=[code, ja]), Q(account=desired_account)).order_by("-updated_at", "-id")
        if qs0.exists():
            return qs0.first()

    qs1 = base.filter(Q(broker__in=[code, ja]), Q(account__in=["SPEC", "NISA"])).order_by("-updated_at", "-id")
    if qs1.exists():
        return qs1.first()

    qs2 = base.filter(broker__in=[code, ja]).order_by("-updated_at", "-id")
    if qs2.exists():
        return qs2.first()

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
# HOLDING 初期出金：source_type の安全取得
# =============================
def _holding_source_type_and_id(holding_id: int):
    """
    CashLedger.SourceType.HOLDING が存在すればそれを使う。
    無ければ SYSTEM を使いつつ、source_id は 10_000_000 + holding.id にオフセットして衝突回避。
    """
    st = getattr(CashLedger.SourceType, "HOLDING", None)
    if st is not None:
        return st, holding_id
    return CashLedger.SourceType.SYSTEM, int(10_000_000 + int(holding_id))


# =============================
# 同期ロジック：配当
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

        holding = getattr(d, "holding", None) or _find_holding(
            d.broker, d.ticker, desired_account=getattr(d, "account", None)
        )

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


# =============================
# 同期ロジック：実損（現物・信用 含む）
# =============================
def sync_from_realized() -> dict:
    """
    実損（売買・受渡）：
      - 対象: SPEC/NISA/MARGIN すべて
      - 現物/NISA: 受渡キャッシュフローの円換算（cashflow_calc_jpy）を Ledger に記録
      - 信用: 投資家PnLの円換算（pnl_jpy）だけを Ledger に記録
      - 日付: r.trade_at（取引日）
      - 種別: 正なら DEPOSIT, 負なら WITHDRAW
      - Holding: broker/ticker/口座でできるだけ一致を探す
    """
    created = 0
    updated = 0

    for r in RealizedTrade.objects.all():
        acc = _get_account(r.broker)
        if not acc:
            continue

        # ---- 金額の決定ロジック ----
        # 現物/NISA → 受渡キャッシュフロー（円換算）
        # 信用      → PnL（円換算）
        if getattr(r, "account", None) == "MARGIN":
            base_val = getattr(r, "pnl_jpy", None)
        else:
            # cashflow_calc_jpy が無ければ cashflow_calc をそのまま使う保険
            base_val = getattr(r, "cashflow_calc_jpy", None)
            if base_val is None:
                base_val = getattr(r, "cashflow_calc", None)

        delta = _as_int(base_val)
        if delta == 0:
            continue

        kind = CashLedger.Kind.DEPOSIT if delta > 0 else CashLedger.Kind.WITHDRAW
        holding = _find_holding(r.broker, r.ticker, desired_account=getattr(r, "account", None))

        created_now = _upsert_ledger(
            account=acc,
            at=r.trade_at,  # 取引日
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
# 同期ロジック：現物保有の初回出金（SPEC/NISA）
# =============================
def sync_from_holdings() -> dict:
    """
    現物保有（SPEC/NISA）について、
    初回買付相当の金額（avg_cost × quantity）を opened_at（なければ created_at）で
    『WITHDRAW（出金）』として Ledger に upsert する。
    - 金額が 0、または数量が 0 のものはスキップ
    - BrokerAccount は Holding.broker から解決
    - source_type は HOLDING があればそれ、無ければ SYSTEM + ID オフセット
    """
    created = 0
    updated = 0

    qs = Holding.objects.filter(account__in=["SPEC", "NISA"]).order_by("-updated_at", "-id")
    for h in qs:
        qty = int(h.quantity or 0)
        unit = float(h.avg_cost or 0)
        amount_abs = _as_int(qty * unit)
        if amount_abs <= 0:
            continue

        acc = _get_account(h.broker)
        if not acc:
            continue

        # 出金（マイナス）
        amount = -abs(amount_abs)

        # 日付：opened_at 優先、無ければ created_at.date()
        at_date = getattr(h, "opened_at", None)
        if not at_date:
            created_dt = getattr(h, "created_at", None)
            at_date = created_dt.date() if created_dt else None
        if not at_date:
            continue

        st, sid = _holding_source_type_and_id(h.id)

        created_now = _upsert_ledger(
            account=acc,
            at=at_date,
            amount=amount,
            kind=CashLedger.Kind.WITHDRAW,
            memo=f"現物取得 {h.ticker}".strip(),
            source_type=st,
            source_id=sid,
            holding=h,
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
    - 現物保有（SPEC/NISA）：opened_at/created_at で『初回出金』を upsert
    - 配当：支払日で upsert（税引後net）＋ Holding 紐付け
    - 実損：取引日で upsert（現物/NISA=受渡キャッシュフロー / 信用=PnL）＋ Holding 紐付け
    - すべて source_type + source_id で完全 upsert（重複なし、毎回上書き）
    """
    svc.ensure_default_accounts()

    res_hold = sync_from_holdings()
    res_div  = sync_from_dividends()
    res_real = sync_from_realized()

    return {
        "holdings_created":  res_hold["created"],
        "holdings_updated":  res_hold["updated"],
        "dividends_created": res_div["created"],
        "dividends_updated": res_div["updated"],
        "realized_created":  res_real["created"],
        "realized_updated":  res_real["updated"],
    }