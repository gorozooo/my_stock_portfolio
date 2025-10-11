# portfolio/views/home.py
from __future__ import annotations
from datetime import date, timedelta
from collections import defaultdict
from typing import Dict, List, Any, Union, Optional
from decimal import Decimal
from django.shortcuts import render

from ..services.model_resolver import resolve_models
from ..services import advisor as svc_advisor

Number = Union[int, float, Decimal]

def _to_float(v: Optional[Number]) -> float:
    if v is None: return 0.0
    if isinstance(v, Decimal):
        try: return float(v)
        except Exception: return 0.0
    try: return float(v)
    except Exception: return 0.0

def _holdings_snapshot() -> dict:
    resolved = resolve_models("portfolio")
    hold = resolved.get("holding")
    if not hold:
        return dict(total_mv=0, total_cost=0, pnl=0, win_ratio=0, by_sector=[])

    M = hold["model"]
    price_f = hold["price"]
    unit_f  = hold["unit"]
    shares_f= hold["shares"]
    sector_f= hold.get("sector")

    total_mv = total_cost = 0.0
    winners = total_rows = 0
    sector_map: Dict[str, Dict[str,float]] = defaultdict(lambda: {"mv":0.0,"cost":0.0})

    for h in M.objects.all():
        price  = _to_float(getattr(h, price_f, 0))
        unit   = _to_float(getattr(h, unit_f, 0))
        shares = _to_float(getattr(h, shares_f, 0))
        sec    = str(getattr(h, sector_f, "その他")) if sector_f else "その他"

        mv = price * shares
        cost = unit * shares

        total_mv += mv
        total_cost += cost
        total_rows += 1
        if price > unit: winners += 1

        sector_map[sec]["mv"] += mv
        sector_map[sec]["cost"] += cost

    pnl = total_mv - total_cost
    win_ratio = round((winners/total_rows*100) if total_rows else 0, 2)

    by_sector = []
    for sec, d in sector_map.items():
        mv, cost = d["mv"], d["cost"]
        rate = round(((mv - cost)/cost*100) if cost>0 else 0.0, 2)
        by_sector.append({"sector": sec, "mv": round(mv), "rate": rate})
    by_sector.sort(key=lambda x: x["mv"], reverse=True)

    return dict(
        total_mv=round(total_mv),
        total_cost=round(total_cost),
        pnl=round(pnl),
        win_ratio=win_ratio,
        by_sector=by_sector[:10]
    )

def _sum_month(model_info: dict, date_field: str, amount_field: str) -> int:
    if not model_info: return 0
    M = model_info["model"]
    amt_f = amount_field
    dt_f  = date_field
    first = date.today().replace(day=1)
    last  = first + timedelta(days=32)
    total = 0.0
    for obj in M.objects.filter(**{f"{dt_f}__gte": first, f"{dt_f}__lt": last}):
        total += _to_float(getattr(obj, amt_f, 0))
    return round(total)

def _cash_balance(resolved: dict) -> int:
    info = resolved.get("cash")
    if not info: return 0
    M = info["model"]; amt_f = info["amount"]
    total = 0.0
    for c in M.objects.all():
        total += _to_float(getattr(c, amt_f, 0))
    return round(total)

def _cashflow_month_bars(resolved: dict) -> list:
    div = resolved.get("dividend")
    real= resolved.get("realized")
    return [
        {"label":"配当", "value": _sum_month(div, div.get("date","date"), div.get("amount","amount")) if div else 0},
        {"label":"実現益", "value": _sum_month(real, real.get("date","date"), real.get("amount","profit")) if real else 0},
    ]

def _stress_test(delta_index_pct: float, snapshot: dict) -> int:
    beta = 0.9
    return round(snapshot["total_mv"] * (1.0 + beta * delta_index_pct/100.0))

def home(request):
    resolved = resolve_models("portfolio")
    snap = _holdings_snapshot()

    # KPI
    kpis = {
        "total_assets": snap["total_mv"],
        "unrealized_pnl": snap["pnl"],
        "realized_month": _sum_month(resolved.get("realized"), resolved.get("realized",{}).get("date","date"), resolved.get("realized",{}).get("amount","profit")),
        "win_ratio": snap["win_ratio"],
        "cash_balance": _cash_balance(resolved),
    }

    sectors = snap["by_sector"]
    cash_bars = _cashflow_month_bars(resolved)

    ai_note, ai_actions = svc_advisor.summarize(kpis, sectors)
    stressed = _stress_test(-5, snap)

    ctx = dict(
        kpis=kpis, sectors=sectors, cash_bars=cash_bars,
        ai_note=ai_note, ai_actions=ai_actions,
        stressed_default=stressed,
        # デバッグ表示に使える（必要ならテンプレに出せます）
        _resolved_summary = {
            k: {"model": v["model"].__name__, **{kk:v.get(kk) for kk in v if kk!="model"}}
            for k,v in resolved.items()
        }
    )
    return render(request, "home.html", ctx)