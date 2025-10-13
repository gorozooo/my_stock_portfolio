# portfolio/views/home.py
from __future__ import annotations
from datetime import date, timedelta
from collections import defaultdict
from typing import Dict, List, Any, Union, Optional, Tuple
from decimal import Decimal

from django.shortcuts import render
from django.db.models import Sum

from ..services import advisor as svc_advisor
from ..models import Holding, RealizedTrade
from ..models_cash import BrokerAccount, CashLedger

Number = Union[int, float, Decimal]

# ========= Utilities =========
def _to_float(v: Optional[Number]) -> float:
    try:
        if v is None:
            return 0.0
        if isinstance(v, Decimal):
            return float(v)
        return float(v)
    except Exception:
        return 0.0

def _month_bounds(today: Optional[date] = None) -> Tuple[date, date]:
    d = today or date.today()
    first = d.replace(day=1)
    next_first = (first.replace(day=28) + timedelta(days=4)).replace(day=1)
    return first, next_first

# ========= Cash =========
def _cash_balances() -> Dict[str, Any]:
    accounts = list(BrokerAccount.objects.all().prefetch_related("ledgers"))
    cur_totals: Dict[str, int] = defaultdict(int)
    by_broker: Dict[str, int] = defaultdict(int)

    for a in accounts:
        led_sum = a.ledgers.aggregate(total=Sum("amount")).get("total") or 0
        bal = int(a.opening_balance or 0) + int(led_sum)
        currency = a.currency or "JPY"
        cur_totals[currency] += bal
        by_broker[a.broker or "OTHER"] += bal

    by_broker_list = [
        {"broker": b, "cash": int(v), "currency": "JPY"} for b, v in by_broker.items()
    ]
    total_jpy = int(cur_totals.get("JPY", 0))
    return {
        "total": total_jpy,
        "by_broker": by_broker_list,
        "total_by_currency": {k: int(v) for k, v in cur_totals.items()},
    }

# ========= Holdings =========
def _holdings_snapshot() -> dict:
    """
    価格は last_price 優先 → 無ければ avg_cost
    未実現損益 =（現物+信用の評価−取得）= 未実現のみ（実損や配当は含めない）
    """
    holdings = list(Holding.objects.all())

    spot_mv = spot_cost = 0.0
    margin_mv = margin_cost = 0.0

    broker_map: Dict[str, Dict[str, float]] = defaultdict(lambda: {"mv": 0.0, "cost": 0.0})

    for h in holdings:
        qty = _to_float(getattr(h, "quantity", 0))
        unit = _to_float(getattr(h, "avg_cost", 0))
        price = _to_float(getattr(h, "last_price", None)) or unit
        mv = price * qty
        cost = unit * qty

        acc = (getattr(h, "account", "") or "").upper()
        broker = getattr(h, "broker", None) or "OTHER"

        if acc == "MARGIN":
            margin_mv += mv
            margin_cost += cost
        else:
            spot_mv += mv
            spot_cost += cost
            broker_map[broker]["mv"] += mv
            broker_map[broker]["cost"] += cost

    total_unrealized_pnl = (spot_mv + margin_mv) - (spot_cost + margin_cost)

    # 勝率（全期間の実現トレード）
    qs = RealizedTrade.objects.all()
    win = sum(1 for r in qs if _to_float(getattr(r, "pnl", 0)) > 0)
    lose = sum(1 for r in qs if _to_float(getattr(r, "pnl", 0)) < 0)
    total_trades = win + lose
    win_ratio = round((win / total_trades * 100.0) if total_trades else 0.0, 1)

    # セクター（ここでは broker をセクター代替）
    by_sector: List[Dict[str, Any]] = []
    for sec, d in broker_map.items():
        mv, cost = d["mv"], d["cost"]
        rate = round(((mv - cost) / cost * 100) if cost > 0 else 0.0, 2)
        by_sector.append({"sector": sec, "mv": round(mv), "rate": rate})
    by_sector.sort(key=lambda x: x["mv"], reverse=True)

    return dict(
        spot_mv=round(spot_mv),
        spot_cost=round(spot_cost),
        margin_mv=round(margin_mv),
        margin_cost=round(margin_cost),
        unrealized=round(total_unrealized_pnl),
        win_ratio=win_ratio,
        by_sector=by_sector[:10],
    )

# ========= Realized / Div（現金台帳） =========
def _sum_realized_month() -> int:
    first, next_first = _month_bounds()
    qs = CashLedger.objects.filter(
        source_type=CashLedger.SourceType.REALIZED, at__gte=first, at__lt=next_first
    )
    return int(sum(int(x.amount) for x in qs))

def _sum_dividend_month() -> int:
    first, next_first = _month_bounds()
    qs = CashLedger.objects.filter(
        source_type=CashLedger.SourceType.DIVIDEND, at__gte=first, at__lt=next_first
    )
    return int(sum(int(x.amount) for x in qs))

def _sum_realized_cum() -> int:
    return int(
        CashLedger.objects.filter(source_type=CashLedger.SourceType.REALIZED)
        .aggregate(s=Sum("amount")).get("s") or 0
    )

def _sum_dividend_cum() -> int:
    return int(
        CashLedger.objects.filter(source_type=CashLedger.SourceType.DIVIDEND)
        .aggregate(s=Sum("amount")).get("s") or 0
    )

def _invested_capital() -> int:
    opening = int(BrokerAccount.objects.aggregate(total=Sum("opening_balance")).get("total") or 0)
    dep  = int(CashLedger.objects.filter(kind=CashLedger.Kind.DEPOSIT ).aggregate(s=Sum("amount")).get("s") or 0)
    xin  = int(CashLedger.objects.filter(kind=CashLedger.Kind.XFER_IN ).aggregate(s=Sum("amount")).get("s") or 0)
    wdr  = int(CashLedger.objects.filter(kind=CashLedger.Kind.WITHDRAW).aggregate(s=Sum("amount")).get("s") or 0)
    xout = int(CashLedger.objects.filter(kind=CashLedger.Kind.XFER_OUT).aggregate(s=Sum("amount")).get("s") or 0)
    return int(opening + dep + xin - wdr - xout)

# ========= View =========
def home(request):
    snap = _holdings_snapshot()
    cash = _cash_balances()

    # 評価ベースの総資産（現物+信用の評価 + 現金）
    total_eval_assets = int(snap["spot_mv"] + snap["margin_mv"] + cash["total"])

    # 未実現（現物＋信用のみ）
    unrealized_pnl = int(snap["unrealized"])

    # 月間＆累積（現金ベース）
    realized_month  = _sum_realized_month()
    dividend_month  = _sum_dividend_month()
    realized_cum    = _sum_realized_cum()
    dividend_cum    = _sum_dividend_cum()

    # 投下資金・ROI
    invested = _invested_capital()
    # 評価ROI = (評価総資産 - 投下資金)/投下資金
    roi_eval_pct = round(((total_eval_assets - invested) / invested * 100.0), 2) if invested > 0 else None
    # 現金化額：信用は含み損益のみ現金化
    liquidation_value = int(snap["spot_mv"] + (snap["margin_mv"] - snap["margin_cost"]) + cash["total"])
    roi_cash_pct = round(((liquidation_value - invested) / invested * 100.0), 2) if invested > 0 else None

    # 比率
    gross_pos = max(int(snap["spot_mv"] + snap["margin_mv"]), 1)
    breakdown_pct = {
        "spot_pct": round(snap["spot_mv"] / gross_pos * 100, 1),
        "margin_pct": round(snap["margin_mv"] / gross_pos * 100, 1),
    }
    liquidity_rate_pct = max(0.0, round(liquidation_value / total_eval_assets * 100, 1)) if total_eval_assets > 0 else 0.0
    margin_ratio_pct = round(snap["margin_mv"] / gross_pos * 100, 1) if gross_pos > 0 else 0.0

    # リスク警告
    risk_flags: List[str] = []
    if margin_ratio_pct >= 60.0:
        risk_flags.append(f"信用比率が {margin_ratio_pct}%（60%超）。余力とボラに注意。")
    if liquidity_rate_pct < 50.0:
        risk_flags.append(f"流動性が {liquidity_rate_pct}% と低め。現金化余地の確保を検討。")

    # 2段式ROI 乖離によるAI提案フック
    ai_extra: List[str] = []
    if roi_eval_pct is not None and roi_cash_pct is not None:
        if abs(roi_eval_pct - roi_cash_pct) >= 15.0:
            ai_extra.append("評価ROIと現金ROIの乖離が大きい。評価と実際の差を埋めるポジション整理を検討。")

    # KPI
    kpis = {
        "total_assets": total_eval_assets,
        "unrealized_pnl": unrealized_pnl,
        "realized_month": realized_month,
        "dividend_month": dividend_month,
        "cash_total": cash["total"],
        "liquidation": liquidation_value,
        "invested": invested,
        "roi_eval_pct": roi_eval_pct,
        "roi_cash_pct": roi_cash_pct,
        "win_ratio": snap["win_ratio"],
        "realized_cum": realized_cum,
        "dividend_cum": dividend_cum,
        "liquidity_rate_pct": liquidity_rate_pct,
        "margin_ratio_pct": margin_ratio_pct,
    }

    sectors = snap["by_sector"]
    cash_bars = [
        {"label": "配当", "value": dividend_month},
        {"label": "実現益", "value": realized_month},
    ]

    ai_note, ai_actions_base = svc_advisor.summarize(kpis, sectors)
    ai_actions = [*ai_actions_base, *ai_extra]

    stressed_default = int(total_eval_assets * (1 + 0.9 * (-5)/100.0))  # 初期-5%

    ctx = dict(
        kpis=kpis,
        sectors=sectors,
        cash_bars=cash_bars,
        breakdown=dict(
            spot_mv=snap["spot_mv"], spot_cost=snap["spot_cost"],
            margin_mv=snap["margin_mv"], margin_cost=snap["margin_cost"]
        ),
        breakdown_pct=breakdown_pct,
        risk_flags=risk_flags,
        cash_total_by_currency=cash["total_by_currency"],
        stressed_default=stressed_default,
        ai_note=ai_note,
        ai_actions=ai_actions,
    )
    return render(request, "home.html", ctx)