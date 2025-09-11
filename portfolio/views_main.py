# portfolio/views_main.py
from django.db.models import Sum
from django.utils import timezone
from .models import Stock, RealizedProfit, Dividend, CashFlow
from .views import _safe_int, _safe_float, _get_current_price_cached  # 既存の補助関数を流用

def compute_portfolio_totals(*, user):
    """main_page と同じ定義で total_assets 等を dict で返す（スナップ・API共用）"""
    spot_mv = margin_mv = 0.0
    spot_upl = margin_upl = 0.0

    # Stocks
    qs = Stock.objects.all()
    if "user" in {f.name for f in Stock._meta.get_fields()}:
        qs = qs.filter(user=user)

    for s in qs:
        shares = _safe_int(getattr(s, "shares", 0))
        unit   = _safe_float(getattr(s, "unit_price", 0.0))
        try:
            current = _get_current_price_cached(getattr(s, "ticker", ""), fallback=unit)
        except Exception:
            current = unit
        used_price = current if _safe_float(current) > 0 else unit
        total_cost = float(shares) * float(unit)
        pos  = str(getattr(s, "position", "買い") or "")
        acct = str(getattr(s, "account_type", "現物") or "")
        mv = float(used_price) * float(shares)
        upl = (float(unit) - float(used_price)) * float(shares) if pos == "売り" else (mv - total_cost)
        is_spot   = (acct in {"現物", "NISA"}) and (pos != "売り")
        is_margin = (acct == "信用") or (pos == "売り")
        if is_spot:
            spot_mv += mv; spot_upl += upl
        elif is_margin:
            margin_mv += mv; margin_upl += upl
        else:
            spot_mv += mv; spot_upl += upl

    # Cash I/O
    cash_io_total = 0
    if CashFlow:
        cf = CashFlow.objects.all()
        if "user" in {f.name for f in CashFlow._meta.get_fields()}:
            cf = cf.filter(user=user)
        agg = cf.values("flow_type").annotate(total=Sum("amount"))
        for row in agg:
            amt = _safe_int(row.get("total", 0))
            cash_io_total += amt if (row.get("flow_type") or "") == "in" else -amt

    # 現物コスト合計
    spot_cost_total = 0.0
    for s in qs:
        pos  = str(getattr(s, "position", "買い") or "")
        acct = str(getattr(s, "account_type", "現物") or "")
        if (acct in {"現物", "NISA"}) and (pos != "売り"):
            shares = _safe_int(getattr(s, "shares", 0))
            unit   = _safe_float(getattr(s, "unit_price", 0.0))
            spot_cost_total += float(shares) * float(unit)

    # 実現損益
    realized_total = 0
    if RealizedProfit:
        rp = RealizedProfit.objects.all()
        if "user" in {f.name for f in RealizedProfit._meta.get_fields()}:
            rp = rp.filter(user=user)
        realized_total += _safe_int(rp.aggregate(s=Sum("profit_amount")).get("s") or 0)
    if Dividend:
        dq = Dividend.objects.all()
        if "user" in {f.name for f in Dividend._meta.get_fields()}:
            dq = dq.filter(user=user)
        for d in dq:
            net = getattr(d, "net_amount", None)
            if net is not None:
                realized_total += _safe_int(net)
            else:
                realized_total += _safe_int(getattr(d, "gross_amount", 0)) - _safe_int(getattr(d, "tax", 0))

    cash_balance = float(cash_io_total) - float(spot_cost_total) + float(realized_total)
    total_assets = float(spot_mv) + float(spot_upl) + float(margin_upl) + float(cash_balance)

    return dict(
        total_assets=total_assets,
        spot_market_value=spot_mv,
        margin_market_value=margin_mv,
        spot_unrealized_pl=spot_upl,
        margin_unrealized_pl=margin_upl,
        cash_balance=cash_balance,
    )