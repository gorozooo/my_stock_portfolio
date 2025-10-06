# portfolio/services/cash_updater.py
from __future__ import annotations
from typing import Optional
from django.db import transaction

# ← あなたの定義どおりのモデルを直 import
from portfolio.models import Dividend, RealizedTrade
from ..models_cash import BrokerAccount, CashLedger
from . import cash_service as svc


# ───────── ユーティリティ ─────────
def _norm_broker(code: str) -> str:
    """BrokerAccount 検索用にコードを正規化。"""
    if not code:
        return ""
    s = str(code).strip().upper()
    # 許容：日本語/コード表記の両方
    if "RAKUTEN" in s or "楽天" in s:
        return "RAKUTEN"
    if "MATSUI" in s or "松井" in s:
        return "MATSUI"
    if "SBI" in s:
        return "SBI"
    return "OTHER"

def _label_from_code(code: str) -> str:
    """BrokerAccount 側が日本語保存でも拾えるようラベルも返す。"""
    m = {
        "RAKUTEN": "楽天",
        "MATSUI" : "松井",
        "SBI"    : "SBI",
        "OTHER"  : "その他",
    }
    return m.get(code, code)

def _get_account(broker_code: str, currency: str = "JPY") -> Optional[BrokerAccount]:
    """
    BrokerAccount を broker=（コード or 日本語ラベル）で探す。
    デフォ口座が未作成なら作成（cash_service 側で実施）。
    """
    svc.ensure_default_accounts(currency=currency)
    code = _norm_broker(broker_code)
    label = _label_from_code(code)

    # broker にコードを入れている場合／日本語ラベルを入れている場合の両方に対応
    qs = BrokerAccount.objects.filter(currency=currency)
    acc = qs.filter(broker=code).order_by("id").first()
    if acc:
        return acc
    return qs.filter(broker=label).order_by("id").first()

def _exists_token(account: BrokerAccount, token: str) -> bool:
    """CashLedger.memo に識別トークンがあれば既存扱い。"""
    return CashLedger.objects.filter(account=account, memo__icontains=token).exists()


# ───────── 配当 → CashLedger（入金）─────────
def sync_from_dividends() -> int:
    """
    Dividend → CashLedger に入金として反映（idempotent）。
    - 金額は Dividend.net_amount() を使用（UIが税引後入力のため）
    - memo に [DIV:<id>] を入れて重複防止
    """
    created = 0
    for d in Dividend.objects.all().iterator():
        acc = _get_account(d.broker)
        if not acc:
            continue

        amount = int(round(d.net_amount() or 0))
        if amount <= 0:
            continue

        token = f"[DIV:{d.id}]"
        if _exists_token(acc, token):
            continue

        CashLedger.objects.create(
            account=acc,
            amount=amount,
            kind=CashLedger.Kind.DEPOSIT,  # 入金
            memo=f"配当 {token}",
        )
        created += 1
    return created


# ───────── 実現損益 → CashLedger（増減）─────────
def sync_from_realized() -> int:
    """
    RealizedTrade → CashLedger に現金増減として反映（idempotent）。
    - まず cashflow_effective を使用（SELL=＋ / BUY=− / 手数料・税抜き済）
    - memo に [REAL:<id>] を入れて重複防止
    """
    created = 0
    for r in RealizedTrade.objects.all().iterator():
        acc = _get_account(r.broker)
        if not acc:
            continue

        delta = float(r.cashflow_effective or 0.0)
        delta_i = int(round(delta))
        if delta_i == 0:
            continue

        token = f"[REAL:{r.id}]"
        if _exists_token(acc, token):
            continue

        # プラスは入金、マイナスは出金
        kind = CashLedger.Kind.DEPOSIT if delta_i > 0 else CashLedger.Kind.WITHDRAW
        CashLedger.objects.create(
            account=acc,
            amount=abs(delta_i) if delta_i > 0 else -abs(delta_i),  # モデルの仕様に合わせて符号運用している場合はここで調整
            kind=kind,
            memo=f"実現損益 {token}",
        )
        created += 1
    return created


# ───────── 一括同期（トランザクション）─────────
@transaction.atomic
def sync_all() -> dict:
    d = sync_from_dividends()
    r = sync_from_realized()
    return {"dividends_created": d, "realized_created": r}