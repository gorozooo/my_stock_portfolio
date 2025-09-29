# portfolio/services/dividends.py
from __future__ import annotations
from dataclasses import dataclass
from typing import Iterable, List, Dict, Optional

from django.db.models import Q, Sum
from django.utils import timezone

from ..models import Dividend


# ===== QS組み立て =====
def build_user_dividend_qs(user):
    """
    ログインユーザーの配当（Holdingあり or 無し(ティッカー有)）に限定したベースQS。
    """
    return (
        Dividend.objects.select_related("holding")
        .filter(Q(holding__user=user) | Q(holding__isnull=True, ticker__isnull=False))
    )


def apply_filters(qs, *, year: Optional[int]=None, month: Optional[int]=None,
                  broker: Optional[str]=None, account: Optional[str]=None):
    """
    年/月/ブローカー/口座で絞り込み。
    broker/account は Dividend側 or Holding側の値のいずれか一致でヒットさせる。
    """
    if year:
        qs = qs.filter(date__year=year)
    if month:
        qs = qs.filter(date__month=month)
    if broker:
        qs = qs.filter(Q(broker=broker) | Q(broker__isnull=True, holding__broker=broker))
    if account:
        qs = qs.filter(Q(account=account) | Q(account__isnull=True, holding__account=account))
    return qs


# ===== 集計 =====
def sum_kpis(qs) -> Dict[str, float]:
    """
    合計KPIを返す:
      - gross: 税引前合計
      - net  : 税引後合計
      - tax  : 税額合計
      - count: 件数
      - yield_pct: 概算利回り（税引後/元本）※元本=数量×取得単価（Holding/Dividendのどちらかに値があれば採用）
    """
    gross = net = 0.0
    tax = float(qs.aggregate(s=Sum("tax"))["s"] or 0)
    count = qs.count()

    # 合計と概算利回りの元本
    cost_sum = 0.0
    for d in qs:
        try:
            gross += float(d.gross_amount() or 0)
            net   += float(d.net_amount() or 0)
        except Exception:
            pass

        # 元本（KPI用）
        qty = d.quantity or (d.holding.quantity if (d.holding and d.holding.quantity) else None)
        pp  = d.purchase_price or (d.holding.avg_cost if (d.holding and d.holding.avg_cost is not None) else None)
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
        "count": int(count),
        "yield_pct": round(yield_pct, 2),
    }


def group_by_month(qs) -> List[Dict]:
    """
    1..12 の月ごとに {m, gross, net, tax} を返す。
    """
    out = []
    for m in range(1, 13):
        g = n = t = 0.0
        for d in qs.filter(date__month=m):
            try:
                g += float(d.gross_amount() or 0)
                n += float(d.net_amount() or 0)
                t += float(d.tax or 0)
            except Exception:
                pass
        out.append({"m": m, "gross": round(g, 2), "net": round(n, 2), "tax": round(t, 2)})
    return out


def group_by_broker(qs) -> List[Dict]:
    """
    ブローカー別の税引後合計 [{broker, net}] 降順。
    Dividend側に値があればそれを優先、無ければHoldingの値、無ければ OTHER。
    """
    buckets = {}
    for d in qs:
        b = d.broker or (d.holding.broker if d.holding else "") or "OTHER"
        buckets.setdefault(b, 0.0)
        try:
            buckets[b] += float(d.net_amount() or 0)
        except Exception:
            pass
    rows = [{"broker": k, "net": round(v, 2)} for k, v in buckets.items()]
    rows.sort(key=lambda x: x["net"], reverse=True)
    return rows


def top_symbols(qs, n=10) -> List[Dict]:
    """
    税引後合計の上位銘柄 [{label, net}] を返す。
    label は display_ticker（無ければ display_name）。
    """
    buckets = {}
    for d in qs:
        label = d.display_ticker or d.display_name or "—"
        buckets.setdefault(label, 0.0)
        try:
            buckets[label] += float(d.net_amount() or 0)
        except Exception:
            pass
    rows = [{"label": k, "net": round(v, 2)} for k, v in buckets.items()]
    rows.sort(key=lambda x: x["net"], reverse=True)
    return rows[:n]