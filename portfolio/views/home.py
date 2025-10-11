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
    except Exception:
        return 0.0

def _holdings_snapshot() -> dict:
    resolved = resolve_models("portfolio")
    hold = resolved.get("holding")
    if not hold:
        return dict(total_mv=0, total_cost=0, pnl=0, win_ratio=0, by_sector=[])

    M = hold["model"]
    price_f = hold["price"]
    unit_f  = hold.get("unit") or price_f   # ← ここ修正
    shares_f= hold["shares"]
    sector_f= hold.get("sector")

    total_mv = total_cost = 0.0
    winners = total_rows = 0
    sector_map: Dict[str, Dict[str,float]] = defaultdict(lambda: {"mv":0.0,"cost":0.0})

    for h in M.objects.all():
        # 安全に取得
        price  = _to_float(getattr(h, price_f, 0))
        unit   = _to_float(getattr(h, unit_f, price))   # ← ここも修正
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