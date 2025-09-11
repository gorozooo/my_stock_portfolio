from __future__ import annotations
from django.utils import timezone
from django.db.models import Sum

try:
    from ..models import Stock, RealizedProfit, Dividend, CashFlow, AssetSnapshot
except Exception:
    Stock = RealizedProfit = Dividend = CashFlow = AssetSnapshot = None  # type: ignore


def _safe_float(x, default=0.0):
    try:
        f = float(x)
        return f if f == f else default
    except Exception:
        return default

def _safe_int(x, default=0):
    try:
        return int(x)
    except Exception:
        try:
            return int(float(x))
        except Exception:
            return default


def _get_current_price_fallback(stock) -> float:
    """
    スナップショットでは外部APIに依存しないよう、
    保存済み current_price（>0）があれば採用、無ければ unit_price を採用。
    """
    cp = _safe_float(getattr(stock, "current_price", 0.0))
    if cp and cp > 0:
        return cp
    return _safe_float(getattr(stock, "unit_price", 0.0))


def compute_portfolio_totals(user) -> dict:
    """
    main_page の確定ロジックを、外部APIなしで/日次用に簡潔化。
    戻り値の dict をそのまま AssetSnapshot に保存できる形にします。
    """
    spot_mv = margin_mv = 0.0
    spot_upl = margin_upl = 0.0

    if Stock:
        qs = Stock.objects.all()
        # user フィルタ（あれば）
        try:
            if "user" in {f.name for f in Stock._meta.get_fields()}:
                qs = qs.filter(user=user)
        except Exception:
            pass

        for s in qs:
            shares = _safe_int(getattr(s, "shares", 0))
            unit   = _safe_float(getattr(s, "unit_price", 0.0))
            current = _get_current_price_fallback(s)  # ← 保存値のみで決定

            used_price = current if _safe_float(current) > 0 else unit
            total_cost = float(shares) * float(unit)

            pos  = str(getattr(s, "position", "買い") or "")
            acct = str(getattr(s, "account_type", "現物") or "")

            mv = float(used_price) * float(shares)
            if pos == "売り":
                upl = (float(unit) - float(used_price)) * float(shares)
            else:
                upl = mv - total_cost

            is_spot   = (acct in {"現物", "NISA"}) and (pos != "売り")
            is_margin = (acct == "信用") or (pos == "売り")

            if is_spot:
                spot_mv  += mv
                spot_upl += upl
            elif is_margin:
                margin_mv  += mv
                margin_upl += upl
            else:
                spot_mv  += mv
                spot_upl += upl

    # 入出金（入金−出金）
    cash_io_total = 0
    if CashFlow:
        cf = CashFlow.objects.all()
        try:
            if "user" in {f.name for f in CashFlow._meta.get_fields()}:
                cf = cf.filter(user=user)
        except Exception:
            pass
        for row in cf.values("flow_type").annotate(total=Sum("amount")):
            amt = _safe_int(row.get("total", 0))
            if (row.get("flow_type") or "") == "in":
                cash_io_total += amt
            else:
                cash_io_total -= amt

    # 現物/NISA 取得額（残株ベース）
    spot_cost_total = 0.0
    if Stock:
        qs2 = Stock.objects.all()
        try:
            if "user" in {f.name for f in Stock._meta.get_fields()}:
                qs2 = qs2.filter(user=user)
        except Exception:
            pass
        for s in qs2:
            pos  = str(getattr(s, "position", "買い") or "")
            acct = str(getattr(s, "account_type", "現物") or "")
            if (acct in {"現物", "NISA"}) and (pos != "売り"):
                shares = _safe_int(getattr(s, "shares", 0))
                unit   = _safe_float(getattr(s, "unit_price", 0.0))
                spot_cost_total += float(shares) * float(unit)

    # 実現損益（売買 + 配当）
    realized_total = 0
    if RealizedProfit:
        rp = RealizedProfit.objects.all()
        try:
            if "user" in {f.name for f in RealizedProfit._meta.get_fields()}:
                rp = rp.filter(user=user)
        except Exception:
            pass
        val = rp.aggregate(s=Sum("profit_amount")).get("s")
        realized_total += _safe_int(val or 0)

    if Dividend:
        dq = Dividend.objects.all()
        try:
            if "user" in {f.name for f in Dividend._meta.get_fields()}:
                dq = dq.filter(user=user)
        except Exception:
            pass
        for d in dq:
            net = getattr(d, "net_amount", None)
            if net is not None:
                realized_total += _safe_int(net)
            else:
                realized_total += _safe_int(getattr(d, "gross_amount", 0)) - _safe_int(getattr(d, "tax", 0))

    # キャッシュ残高
    cash_balance = float(cash_io_total) - float(spot_cost_total) + float(realized_total)

    # 総資産
    unrealized_total = spot_upl + margin_upl
    total_assets = float(spot_mv) + float(unrealized_total) + float(cash_balance)

    return dict(
        total_assets=int(round(total_assets)),
        spot_market_value=int(round(spot_mv)),
        margin_market_value=int(round(margin_mv)),
        cash_balance=int(round(cash_balance)),
        unrealized_pl_total=int(round(unrealized_total)),
    )


def save_daily_snapshot(user) -> None:
    """
    当日分のスナップショットを upsert（update_or_create）します。
    """
    if not AssetSnapshot:
        return
    today = timezone.localdate()
    totals = compute_portfolio_totals(user)
    try:
        AssetSnapshot.objects.update_or_create(
            user=user,
            date=today,
            defaults=totals,
        )
    except Exception:
        # user フィールドなし等は無視
        pass
