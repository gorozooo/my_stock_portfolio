# portfolio/views/home.py
from __future__ import annotations
from datetime import date, timedelta
from collections import defaultdict
from typing import Dict, List, Any

from django.db.models import Sum, F, FloatField
from django.db.models.functions import Coalesce
from django.shortcuts import render

# 既存モデル名に合わせて import。無い場合は try で握りつぶす
try:
    from ..models import Holding, RealizedTrade, Dividend, Cash
except Exception:
    Holding = RealizedTrade = Dividend = Cash = None  # type: ignore

from ..services import advisor as svc_advisor

def _safe(v, default=0.0):
    try:
        return float(v or 0)
    except Exception:
        return default

def _holdings_snapshot():
    if not Holding:
        return dict(total_mv=0, total_cost=0, pnl=0, win_ratio=0, by_sector=[])
    qs = Holding.objects.all()

    # 評価額 / 取得額 / 含み損益
    total_mv = _safe(qs.aggregate(x=Coalesce(Sum(F("current_price")*F("shares"), output_field=FloatField()), 0.0))["x"])
    total_cost = _safe(qs.aggregate(x=Coalesce(Sum(F("unit_price")*F("shares"), output_field=FloatField()), 0.0))["x"])
    pnl = total_mv - total_cost

    # 含み益銘柄比率（勝率）
    winners = qs.filter(current_price__gt=F("unit_price")).count()
    total_names = qs.count()
    win_ratio = round((winners / total_names * 100) if total_names else 0, 2)

    # セクター別（評価額・損益率）
    sector_map: Dict[str, Dict[str, float]] = defaultdict(lambda: {"mv": 0.0, "cost": 0.0})
    for h in qs:
        mv = _safe(h.current_price) * _safe(h.shares)
        cost = _safe(h.unit_price) * _safe(h.shares)
        sector_map[getattr(h, "sector", "その他")]["mv"] += mv
        sector_map[getattr(h, "sector", "その他")]["cost"] += cost

    by_sector = []
    for sec, d in sector_map.items():
        rate = 0.0
        if d["cost"] > 0:
            rate = round((d["mv"] - d["cost"]) / d["cost"] * 100, 2)
        by_sector.append({"sector": sec, "mv": round(d["mv"]), "rate": rate})

    # MV比率で並べ替え、上位のみ横スク用
    by_sector.sort(key=lambda x: x["mv"], reverse=True)
    return dict(
        total_mv=round(total_mv),
        total_cost=round(total_cost),
        pnl=round(pnl),
        win_ratio=win_ratio,
        by_sector=by_sector[:10],
    )

def _realized_month():
    if not RealizedTrade:
        return dict(realized_month=0)
    # 当月の実現損益
    first = date.today().replace(day=1)
    qs = RealizedTrade.objects.filter(close_date__gte=first, close_date__lt=first + timedelta(days=32))
    realized = _safe(qs.aggregate(x=Coalesce(Sum("profit_amount"), 0.0))["x"])
    return dict(realized_month=round(realized))

def _dividend_month():
    if not Dividend:
        return dict(dividend_month=0)
    first = date.today().replace(day=1)
    qs = Dividend.objects.filter(received_date__gte=first, received_date__lt=first + timedelta(days=32))
    amt = _safe(qs.aggregate(x=Coalesce(Sum("amount"), 0.0))["x"])
    return dict(dividend_month=round(amt))

def _cash_balance():
    if not Cash:
        return dict(cash_balance=0)
    bal = _safe(Cash.objects.aggregate(x=Coalesce(Sum("amount"), 0.0))["x"])
    return dict(cash_balance=round(bal))

def _cashflow_month_bars():
    """入出金（購入/売却/配当/入金/出金）を簡易棒グラフ用に"""
    bars = [
        {"label":"配当", "value": _dividend_month()["dividend_month"]},
        {"label":"実現益", "value": _realized_month()["realized_month"]},
        # 必要なら購入/売却や入金/出金もモデルに合わせて追加
    ]
    return bars

def _stress_test(delta_index_pct: float, snapshot: dict) -> float:
    """
    日経▲5%時などの簡易ストレステスト。
    仮定：ポートフォリオβ=0.9 として MV*(1+0.9*ΔIndex) で近似。
    """
    beta = 0.9
    return round(snapshot["total_mv"] * (1.0 + beta * delta_index_pct/100.0))

def home(request):
    snap = _holdings_snapshot()
    kpis = {
        "total_assets": snap["total_mv"],         # 総資産（評価）
        "unrealized_pnl": snap["pnl"],           # 含み損益
        "realized_month": _realized_month()["realized_month"],  # 当月実現
        "win_ratio": snap["win_ratio"],          # 含み益銘柄比率
        "cash_balance": _cash_balance()["cash_balance"],
    }
    sectors = snap["by_sector"]
    cash_bars = _cashflow_month_bars()

    # AIアドバイザー（ルールベース→後でGPTに差し替え）
    ai_note, ai_actions = svc_advisor.summarize(kpis, sectors)

    # デフォルトのストレステスト（-5%）
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