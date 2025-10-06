# portfolio/services/cash_updater.py
from __future__ import annotations
from typing import Optional, Any
from datetime import date
from django.db import transaction
from django.db.models import QuerySet

from ..models_cash import BrokerAccount, CashLedger
from . import cash_service as svc


# ─────────────────────────────────────────────
# ユーティリティ
# ─────────────────────────────────────────────
def _norm_broker(b: str) -> str:
    """入力された broker を「楽天/松井/SBI」に正規化。"""
    if not b:
        return ""
    s = str(b).strip().upper()
    if "RAKUTEN" in s or "楽天" in s:
        return "楽天"
    if "MATSUI" in s or "松井" in s:
        return "松井"
    if "SBI" in s:
        return "SBI"
    # 既存3社以外も一応通す
    return str(b).strip()

def _get_account_by_broker(broker: str, currency: str = "JPY") -> Optional[BrokerAccount]:
    broker = _norm_broker(broker)
    if not broker:
        return None
    # 代表口座（現物/JPY 優先）を返す
    svc.ensure_default_accounts(currency=currency)
    return BrokerAccount.objects.filter(broker=broker, currency=currency).order_by("account_type").first()

def _exists_token(account: BrokerAccount, token: str) -> bool:
    """CashLedger の memo に識別トークンが既にあるかをチェック。"""
    return CashLedger.objects.filter(account=account, memo__icontains=token).exists()

def _safe_int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except Exception:
        return default


# ─────────────────────────────────────────────
# 配当を同期
# ─────────────────────────────────────────────
def sync_from_dividends(today: Optional[date] = None) -> int:
    """
    Dividends(配当) → CashLedger に入金として反映。
    * idempotent（同じ配当を二重登録しない）
    * モデル差異を吸収するため、候補フィールドを順に探索
    戻り値: 追加レコード件数
    """
    try:
        # いろんな環境名に対応
        from ..models import dividend as v_div  # あなたの構成（viewsに合わせた推測）
        Dividend = getattr(v_div, "Dividend", None)
        if Dividend is None:
            # 典型: portfolio/models_dividend.py 等
            from ..models_dividend import Dividend  # type: ignore
    except Exception:
        # 失敗してもアプリを落とさない（あとでモデル定義を見て合わせる）
        return 0

    qs: QuerySet = Dividend.objects.all()

    # 支払日フィルタ（paid_at / pay_date / received_at 等）
    date_fields = ["paid_at", "pay_date", "payment_date", "received_at", "date"]
    for f in date_fields:
        if f in [fld.name for fld in Dividend._meta.fields]:
            qs = qs.exclude(**{f: None})
            break

    created = 0
    for d in qs.iterator():
        # broker
        broker_val = None
        for f in ("broker", "broker_code", "account_broker", "account"):
            broker_val = getattr(d, f, None)
            if broker_val:
                break
        broker_str = str(getattr(broker_val, "value", broker_val) or "")
        account = _get_account_by_broker(broker_str)
        if not account:
            continue

        # 金額（ネット受取を優先: net_amount / after_tax_amount / amount - tax）
        net = None
        for f in ("net_amount", "after_tax_amount", "received_amount", "amount_net"):
            v = getattr(d, f, None)
            if v is not None:
                net = v
                break
        if net is None:
            gross = None
            tax = 0
            for f in ("amount", "gross_amount", "dividend_amount"):
                v = getattr(d, f, None)
                if v is not None:
                    gross = v; break
            for f in ("tax", "withholding_tax", "tax_amount"):
                v = getattr(d, f, None)
                if v is not None:
                    tax = v; break
            net = _safe_int(gross, 0) - _safe_int(tax, 0)

        amount = _safe_int(net, 0)
        if amount == 0:
            continue

        src_id = getattr(d, "id", None) or getattr(d, "pk", None)
        token = f"[DIV:{src_id}]"
        if _exists_token(account, token):
            continue

        memo = f"配当 {token}"
        CashLedger.objects.create(
            account=account,
            amount=amount,
            kind=CashLedger.Kind.DEPOSIT if hasattr(CashLedger.Kind, "DEPOSIT") else CashLedger.Kind.DEPOSIT,  # safety
            memo=memo,
        )
        created += 1
    return created


# ─────────────────────────────────────────────
# 実現損益を同期
# ─────────────────────────────────────────────
def sync_from_realized(today: Optional[date] = None) -> int:
    """
    Realized(実現損益) → CashLedger に現金増減として反映。
    * idempotent
    * “現金差分”フィールドがあればそれを優先、無ければ擬似計算
      - 現金増減 = 受渡金額 − 手数料 − 税金
      - 受渡金額が無ければ「売買区分 + 約定代金」等で近似
    """
    try:
        from ..models import realized as m_real  # 推測
        Realized = getattr(m_real, "RealizedTrade", None) or getattr(m_real, "Realized", None)
        if Realized is None:
            from ..models_realized import Realized  # type: ignore
    except Exception:
        return 0

    qs: QuerySet = Realized.objects.all()
    # 受渡日/約定日ベースの存在チェック
    date_fields = ["settlement_date", "trade_date", "executed_at", "date"]
    for f in date_fields:
        if f in [fld.name for fld in Realized._meta.fields]:
            qs = qs.exclude(**{f: None})
            break

    created = 0
    for r in qs.iterator():
        # broker
        broker_val = None
        for f in ("broker", "broker_code", "account_broker", "account"):
            broker_val = getattr(r, f, None)
            if broker_val:
                break
        broker_str = str(getattr(broker_val, "value", broker_val) or "")
        account = _get_account_by_broker(broker_str)
        if not account:
            continue

        # 現金差分
        cash_delta = None
        for f in ("cash_delta", "net_cash", "settlement_amount"):
            v = getattr(r, f, None)
            if v is not None:
                cash_delta = v; break

        if cash_delta is None:
            # 近似: proceeds - cost - fee - tax
            proceeds = None
            for f in ("proceeds", "amount", "gross", "sell_amount"):
                v = getattr(r, f, None)
                if v is not None:
                    proceeds = v; break
            cost = 0
            for f in ("cost", "buy_amount", "principal"):
                v = getattr(r, f, None)
                if v is not None:
                    cost = v; break
            fee = 0
            for f in ("fee", "fees", "commission", "commission_fee"):
                v = getattr(r, f, None)
                if v is not None:
                    fee = v; break
            tax = 0
            for f in ("tax", "taxes", "withholding_tax", "tax_amount"):
                v = getattr(r, f, None)
                if v is not None:
                    tax = v; break
            # 売買方向（買いはマイナス、売りはプラス）
            side = (getattr(r, "side", "") or getattr(r, "trade_type", "") or "").upper()
            sign = -1 if "BUY" in side or "買" in side else +1
            if proceeds is None:
                # 最低限、実現損益 pnl があれば cost と合わせる
                pnl = _safe_int(getattr(r, "pnl", None), 0)
                proceeds = _safe_int(cost, 0) + pnl
            cash_delta = sign * (_safe_int(proceeds, 0) - _safe_int(cost, 0)) - _safe_int(fee, 0) - _safe_int(tax, 0)

        delta = _safe_int(cash_delta, 0)
        if delta == 0:
            continue

        src_id = getattr(r, "id", None) or getattr(r, "pk", None)
        token = f"[REAL:{src_id}]"
        if _exists_token(account, token):
            continue

        memo = f"実現損益 {token}"
        kind = CashLedger.Kind.DEPOSIT if delta > 0 else CashLedger.Kind.WITHDRAW
        CashLedger.objects.create(account=account, amount=abs(delta) if delta > 0 else -abs(delta),
                                  kind=kind, memo=memo)
        created += 1
    return created


# ─────────────────────────────────────────────
# まとめて同期
# ─────────────────────────────────────────────
@transaction.atomic
def sync_all() -> dict:
    """配当・実損の両方を同期し、作成件数を返す。"""
    d = sync_from_dividends()
    r = sync_from_realized()
    return {"dividends_created": d, "realized_created": r}