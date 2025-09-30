# portfolio/services/dividends.py
from __future__ import annotations
from typing import Optional, List, Dict

from django.db.models import Q, Sum
from ..models import Dividend

# ========== ベース QS ==========
def build_user_dividend_qs(user):
    return (
        Dividend.objects.select_related("holding")
        .filter(Q(holding__user=user) | Q(holding__isnull=True, ticker__isnull=False))
    )

def apply_filters(qs, *, year: Optional[int]=None, month: Optional[int]=None,
                  broker: Optional[str]=None, account: Optional[str]=None):
    if year:
        qs = qs.filter(date__year=year)
    if month:
        qs = qs.filter(date__month=month)
    if broker:
        qs = qs.filter(Q(broker=broker) | Q(broker__isnull=True, holding__broker=broker))
    if account:
        qs = qs.filter(Q(account=account) | Q(account__isnull=True, holding__account=account))
    return qs

# 一回だけ評価して複数集計で使い回す
def materialize(qs) -> List[Dividend]:
    return list(qs)

# ========== 集計 ==========
def sum_kpis(qs_or_list) -> Dict[str, float]:
    rows = materialize(qs_or_list) if not isinstance(qs_or_list, list) else qs_or_list
    gross = net = tax = 0.0
    for d in rows:
        try:
            gross += float(d.gross_amount() or 0)
            net   += float(d.net_amount() or 0)
            tax   += float(d.tax or 0)
        except Exception:
            pass

    # 概算利回り（数量×取得単価のある明細のみを元本に加算）
    cost_sum = 0.0
    for d in rows:
        qty = d.quantity or (d.holding.quantity if d.holding and d.holding.quantity else None)
        pp  = d.purchase_price or (d.holding.avg_cost if d.holding and d.holding.avg_cost is not None else None)
        if qty and pp is not None:
            try:
                cost_sum += float(qty) * float(pp)
            except Exception:
                pass

    yield_pct = (net / cost_sum * 100.0) if cost_sum > 0 else 0.0
    return {
        "gross": round(gross, 2),
        "net":   round(net, 2),
        "tax":   round(tax, 2),
        "count": len(rows),
        "yield_pct": round(yield_pct, 2),
    }

def group_by_month(qs_or_list) -> List[Dict]:
    rows = materialize(qs_or_list) if not isinstance(qs_or_list, list) else qs_or_list
    out = []
    for m in range(1, 13):
        g = n = t = 0.0
        for d in rows:
            if d.date and d.date.month == m:
                try:
                    g += float(d.gross_amount() or 0)
                    n += float(d.net_amount() or 0)
                    t += float(d.tax or 0)
                except Exception:
                    pass
        out.append({"m": m, "gross": round(g, 2), "net": round(n, 2), "tax": round(t, 2)})
    return out

def group_by_broker(qs_or_list) -> List[Dict]:
    rows = materialize(qs_or_list) if not isinstance(qs_or_list, list) else qs_or_list
    buckets = {}
    for d in rows:
        b = d.broker or (d.holding.broker if d.holding else "") or "OTHER"
        buckets.setdefault(b, 0.0)
        try:
            buckets[b] += float(d.net_amount() or 0)
        except Exception:
            pass
    out = [{"broker": k, "net": round(v, 2)} for k, v in buckets.items()]
    out.sort(key=lambda x: x["net"], reverse=True)
    return out

def group_by_account(qs_or_list) -> List[Dict]:
    rows = materialize(qs_or_list) if not isinstance(qs_or_list, list) else qs_or_list
    buckets = {}
    for d in rows:
        a = d.account or (d.holding.account if d.holding else "") or "SPEC"
        buckets.setdefault(a, 0.0)
        try:
            buckets[a] += float(d.net_amount() or 0)
        except Exception:
            pass
    out = [{"account": k, "net": round(v, 2)} for k, v in buckets.items()]
    out.sort(key=lambda x: x["net"], reverse=True)
    return out

def top_symbols(qs_or_list, n=10) -> List[Dict]:
    rows = materialize(qs_or_list) if not isinstance(qs_or_list, list) else qs_or_list
    buckets = {}
    for d in rows:
        label = d.display_ticker or d.display_name or "—"
        buckets.setdefault(label, 0.0)
        try:
            buckets[label] += float(d.net_amount() or 0)
        except Exception:
            pass
    out = [{"label": k, "net": round(v, 2)} for k, v in buckets.items()]
    out.sort(key=lambda x: x["net"], reverse=True)
    return out[:n]