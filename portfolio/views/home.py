# portfolio/views/home.py
from __future__ import annotations
from datetime import date, timedelta
from collections import defaultdict
from typing import Dict, List, Any, Union, Optional, Tuple
from decimal import Decimal
from django.shortcuts import render

from ..services.model_resolver import resolve_models
from ..services import advisor as svc_advisor
from ..models import Holding, RealizedTrade, Dividend  # ← モデル明示

Number = Union[int, float, Decimal]


# =========================
# ユーティリティ
# =========================
def _to_float(v: Optional[Number]) -> float:
    if v is None:
        return 0.0
    if isinstance(v, Decimal):
        try:
            return float(v)
        except Exception:
            return 0.0
    try:
        return float(v)
    except Exception:
        return 0.0


# =========================
# 保有スナップショット
# =========================
def _holdings_snapshot() -> dict:
    total_mv = 0.0
    total_cost = 0.0
    winners = 0
    total_rows = 0
    sector_map: Dict[str, Dict[str, float]] = defaultdict(lambda: {"mv": 0.0, "cost": 0.0})

    for h in Holding.objects.all():
        qty = _to_float(h.quantity)
        cost = _to_float(h.avg_cost)
        mv = qty * cost  # 評価額（リアルタイム価格取得は別途）
        total_mv += mv
        total_cost += cost * qty
        total_rows += 1
        if mv > cost * qty:
            winners += 1
        sector_map[h.broker]["mv"] += mv
        sector_map[h.broker]["cost"] += cost * qty

    pnl = total_mv - total_cost
    win_ratio = round((winners / total_rows * 100) if total_rows else 0.0, 2)

    by_sector = []
    for sec, d in sector_map.items():
        mv, cost = d["mv"], d["cost"]
        rate = round(((mv - cost) / cost * 100) if cost else 0.0, 2)
        by_sector.append({"sector": sec, "mv": round(mv), "rate": rate})
    by_sector.sort(key=lambda x: x["mv"], reverse=True)

    return dict(
        total_mv=round(total_mv),
        total_cost=round(total_cost),
        pnl=round(pnl),
        win_ratio=win_ratio,
        by_sector=by_sector,
    )


# =========================
# 月次範囲
# =========================
def _month_bounds(today: Optional[date] = None) -> Tuple[date, date]:
    d = today or date.today()
    first = d.replace(day=1)
    next_first = (first.replace(day=28) + timedelta(days=4)).replace(day=1)
    return first, next_first


# =========================
# 実現損益
# =========================
def _sum_realized_month() -> float:
    first, next_first = _month_bounds()
    total = 0.0
    for r in RealizedTrade.objects.filter(trade_at__gte=first, trade_at__lt=next_first):
        total += _to_float(r.pnl)
    return round(total)


# =========================
# 配当（月次合計）
# =========================
def _sum_dividend_month() -> float:
    first, next_first = _month_bounds()
    total = 0.0
    for d in Dividend.objects.filter(date__gte=first, date__lt=next_first):
        total += _to_float(d.amount)
    return round(total)


# =========================
# 現金残高（将来Cashモデル対応）
# =========================
def _cash_balance() -> float:
    # まだCashモデル未定義なら0固定
    return 0.0


# =========================
# キャッシュフロー棒グラフ
# =========================
def _cashflow_month_bars() -> list:
    return [
        {"label": "配当", "value": _sum_dividend_month()},
        {"label": "実現益", "value": _sum_realized_month()},
    ]


# =========================
# ストレステスト
# =========================
def _stress_test(delta_index_pct: float, snapshot: dict) -> int:
    beta = 0.9
    return round(snapshot["total_mv"] * (1.0 + beta * delta_index_pct / 100.0))


# =========================
# View
# =========================
def home(request):
    snap = _holdings_snapshot()

    kpis = {
        "total_assets": snap["total_mv"],
        "unrealized_pnl": snap["pnl"],
        "realized_month": _sum_realized_month(),
        "win_ratio": snap["win_ratio"],
        "cash_balance": _cash_balance(),
    }

    sectors = snap["by_sector"]
    cash_bars = _cashflow_month_bars()
    ai_note, ai_actions = svc_advisor.summarize(kpis, sectors)
    stressed = _stress_test(-5, snap)

    ctx = dict(
        kpis=kpis,
        sectors=sectors,
        cash_bars=cash_bars,
        ai_note=ai_note,
        ai_actions=ai_actions,
        stressed_default=stressed,
    )

    return render(request, "home.html", ctx)