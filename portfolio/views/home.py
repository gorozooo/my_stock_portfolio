# portfolio/views/home.py
from __future__ import annotations
from datetime import date, timedelta
from collections import defaultdict
from typing import Dict, List, Any, Optional, Tuple, Union

from django.shortcuts import render
from decimal import Decimal

# 既存モデル名に合わせて import。無い場合は握りつぶし
try:
    from ..models import Holding, RealizedTrade, Dividend, Cash
except Exception:
    Holding = RealizedTrade = Dividend = Cash = None  # type: ignore

from ..services import advisor as svc_advisor

Number = Union[int, float, Decimal]

# ---------- 共通ユーティリティ ----------
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

def _get_attr_any(obj: Any, names: List[str], default: float = 0.0) -> float:
    """候補名のうち最初に見つかった属性をfloatで返す"""
    for n in names:
        if hasattr(obj, n):
            return _to_float(getattr(obj, n))
    return default

def _get_str_any(obj: Any, names: List[str], default: str = "") -> str:
    for n in names:
        if hasattr(obj, n):
            v = getattr(obj, n)
            return "" if v is None else str(v)
    return default

# ---------- スナップショット（安全版・Python合算） ----------
def _holdings_snapshot() -> dict:
    """
    DB集計ではなくPython側で合算。
    フィールド名のゆらぎに対応：
      - 価格: current_price / price / last_price
      - 取得単価: unit_price / buy_price / cost_price / average_price
      - 株数: shares / quantity / qty / amount
      - セクター: sector / sector_name / industry / category
    """
    if not Holding:
        return dict(total_mv=0, total_cost=0, pnl=0, win_ratio=0, by_sector=[])

    qs = Holding.objects.all()

    total_mv = 0.0
    total_cost = 0.0
    winners = 0
    total_rows = 0

    sector_map: Dict[str, Dict[str, float]] = defaultdict(lambda: {"mv": 0.0, "cost": 0.0})

    for h in qs:
        price = _get_attr_any(h, ["current_price", "price", "last_price"])
        unit = _get_attr_any(h, ["unit_price", "buy_price", "cost_price", "average_price"])
        shares = _get_attr_any(h, ["shares", "quantity", "qty", "amount"])
        sec = _get_str_any(h, ["sector", "sector_name", "industry", "category"], "その他") or "その他"

        mv = price * shares
        cost = unit * shares

        total_mv += mv
        total_cost += cost
        total_rows += 1
        if price > unit:
            winners += 1

        sector_map[sec]["mv"] += mv
        sector_map[sec]["cost"] += cost

    pnl = total_mv - total_cost
    win_ratio = round((winners / total_rows * 100) if total_rows else 0, 2)

    by_sector = []
    for sec, d in sector_map.items():
        mv = d["mv"]
        cost = d["cost"]
        rate = round(((mv - cost) / cost * 100) if cost > 0 else 0.0, 2)
        by_sector.append({"sector": sec, "mv": round(mv), "rate": rate})

    by_sector.sort(key=lambda x: x["mv"], reverse=True)

    return dict(
        total_mv=round(total_mv),
        total_cost=round(total_cost),
        pnl=round(pnl),
        win_ratio=win_ratio,
        by_sector=by_sector[:10],
    )

def _realized_month() -> dict:
    if not RealizedTrade:
        return dict(realized_month=0)
    # フィールド名のゆらぎ（profit_amount / profit / pnl / realized）
    first = date.today().replace(day=1)
    qs = RealizedTrade.objects.filter(close_date__gte=first, close_date__lt=first + timedelta(days=32))
    total = 0.0
    for r in qs:
        amt = _get_attr_any(r, ["profit_amount", "profit", "pnl", "realized"])
        total += amt
    return dict(realized_month=round(total))

def _dividend_month() -> dict:
    if not Dividend:
        return dict(dividend_month=0)
    first = date.today().replace(day=1)
    qs = Dividend.objects.filter(received_date__gte=first, received_date__lt=first + timedelta(days=32))
    total = 0.0
    for d in qs:
        total += _get_attr_any(d, ["amount", "value", "gross", "net"])
    return dict(dividend_month=round(total))

def _cash_balance() -> dict:
    if not Cash:
        return dict(cash_balance=0)
    total = 0.0
    for c in Cash.objects.all():
        total += _get_attr_any(c, ["amount", "balance", "value"])
    return dict(cash_balance=round(total))

def _cashflow_month_bars() -> list:
    return [
        {"label": "配当", "value": _dividend_month()["dividend_month"]},
        {"label": "実現益", "value": _realized_month()["realized_month"]},
    ]

def _stress_test(delta_index_pct: float, snapshot: dict) -> float:
    beta = 0.9
    return round(snapshot["total_mv"] * (1.0 + beta * delta_index_pct / 100.0))

def home(request):
    snap = _holdings_snapshot()

    kpis = {
        "total_assets": snap["total_mv"],
        "unrealized_pnl": snap["pnl"],
        "realized_month": _realized_month()["realized_month"],
        "win_ratio": snap["win_ratio"],
        "cash_balance": _cash_balance()["cash_balance"],
    }

    sectors = snap["by_sector"]
    cash_bars = _cashflow_month_bars()

    # AIコメント（スタブ）
    ai_note, ai_actions = svc_advisor.summarize(kpis, sectors)

    stressed = _stress_test(-3, snap)

    ctx = dict(
        kpis=kpis,
        sectors=sectors,
        cash_bars=cash_bars,
        ai_note=ai_note,
        ai_actions=ai_actions,
        stressed_default=stressed,
    )
    return render(request, "home.html", ctx)