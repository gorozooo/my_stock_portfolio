# portfolio/services/dividends.py
from __future__ import annotations
from typing import Optional, List, Dict
from decimal import Decimal

from django.db.models import Q, Sum

from ..models import Dividend, DividendGoal


# ========== ベース QS ==========
def build_user_dividend_qs(user):
    """
    ログインユーザーの配当（Holdingあり or 無し(ティッカー有)）に限定したベースQS。
    """
    return (
        Dividend.objects.select_related("holding")
        .filter(Q(holding__user=user) | Q(holding__isnull=True, ticker__isnull=False))
    )


def apply_filters(
    qs,
    *,
    year: Optional[int] = None,
    month: Optional[int] = None,
    broker: Optional[str] = None,
    account: Optional[str] = None,
    q: Optional[str] = None,
):
    """
    年/月/ブローカー/口座/キーワードで絞り込み。

    - broker/account は Dividend 側の値が優先。無い場合は Holding 側で一致させる。
    - q は DB カラムに対してのみ検索（display_* はプロパティのため不可）。
    """
    if year:
        qs = qs.filter(date__year=year)
    if month:
        qs = qs.filter(date__month=month)
    if broker:
        qs = qs.filter(Q(broker=broker) | Q(broker__isnull=True, holding__broker=broker))
    if account:
        qs = qs.filter(Q(account=account) | Q(account__isnull=True, holding__account=account))
    if q:
        q = q.strip()
        if q:
            qs = qs.filter(
                Q(ticker__icontains=q)
                | Q(name__icontains=q)
                | Q(holding__ticker__icontains=q)
                | Q(holding__name__icontains=q)
            )
    return qs


# 一回だけ評価して複数集計で使い回す
def materialize(qs) -> List[Dividend]:
    return list(qs)


# ========== 集計 ==========
def sum_kpis(qs_or_list) -> Dict[str, float]:
    """
    合計KPI:
      - gross: 税引前合計
      - net  : 税引後合計
      - tax  : 税額合計
      - count: 件数
      - yield_pct: 概算利回り（税引後/元本）
        * 元本 = 数量 × 取得単価（Dividend or Holding のどちらかに値がある明細のみ）
    """
    rows = materialize(qs_or_list) if not isinstance(qs_or_list, list) else qs_or_list

    gross = net = tax = 0.0
    for d in rows:
        try:
            gross += float(d.gross_amount() or 0)
            net += float(d.net_amount() or 0)
            tax += float(d.tax or 0)
        except Exception:
            # どれか欠けてもスキップして続行
            pass

    # 概算利回りの元本
    cost_sum = 0.0
    for d in rows:
        qty = d.quantity or (d.holding.quantity if d.holding and d.holding.quantity else None)
        pp = d.purchase_price or (
            d.holding.avg_cost if d.holding and d.holding.avg_cost is not None else None
        )
        if qty and pp is not None:
            try:
                cost_sum += float(qty) * float(pp)
            except Exception:
                pass

    yield_pct = (net / cost_sum * 100.0) if cost_sum > 0 else 0.0

    return {
        "gross": round(gross, 2),
        "net": round(net, 2),
        "tax": round(tax, 2),
        "count": len(rows),
        "yield_pct": round(yield_pct, 2),
    }


def group_by_month(qs_or_list) -> List[Dict]:
    """
    1..12 の月ごとに {m, gross, net, tax} を返す。
    （年で絞る場合は apply_filters で year を渡してから呼ぶ）
    """
    rows = materialize(qs_or_list) if not isinstance(qs_or_list, list) else qs_or_list
    out: List[Dict] = []
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
    """
    ブローカー別の税引後合計 [{broker, net}]（降順）。
    Dividend.broker → なければ Holding.broker → それも無ければ "OTHER"
    """
    rows = materialize(qs_or_list) if not isinstance(qs_or_list, list) else qs_or_list
    buckets: Dict[str, float] = {}
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
    """
    口座区分別の税引後合計 [{account, net}]（降順）。
    Dividend.account → なければ Holding.account → それも無ければ "SPEC"
    """
    rows = materialize(qs_or_list) if not isinstance(qs_or_list, list) else qs_or_list
    buckets: Dict[str, float] = {}
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


def top_symbols(qs_or_list, n: int = 10) -> List[Dict]:
    """
    税引後合計の上位銘柄 [{label, net}] を返す（降順で n 件）。
    label は display_ticker（無ければ display_name）。
    """
    rows = materialize(qs_or_list) if not isinstance(qs_or_list, list) else qs_or_list
    buckets: Dict[str, float] = {}
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
    
# ========== 目標（年間） ==========
def get_goal_amount(user, year: int) -> Decimal:
    try:
        g = DividendGoal.objects.get(user=user, year=year)
        return g.amount or Decimal("0")
    except DividendGoal.DoesNotExist:
        return Decimal("0")

def set_goal_amount(user, year: int, amount: Decimal) -> Decimal:
    obj, _ = DividendGoal.objects.update_or_create(
        user=user, year=year, defaults={"amount": amount}
    )
    return obj.amount or Decimal("0")
