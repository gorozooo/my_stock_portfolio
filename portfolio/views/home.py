# portfolio/views/home.py
from __future__ import annotations
from datetime import date, timedelta
from collections import defaultdict
from typing import Dict, List, Any, Union, Optional, Tuple
from decimal import Decimal
from django.shortcuts import render

from ..services import advisor as svc_advisor
from ..services.price_provider import get_prices
from ..models import Holding, RealizedTrade, Dividend

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
# 月次範囲
# =========================
def _month_bounds(today: Optional[date] = None) -> Tuple[date, date]:
    d = today or date.today()
    first = d.replace(day=1)
    next_first = (first.replace(day=28) + timedelta(days=4)).replace(day=1)
    return first, next_first


# =========================
# 保有スナップショット（現物のみを総資産に採用）
# =========================
def _holdings_snapshot() -> dict:
    """
    - 総資産は現物のみ（account != 'MARGIN'）
    - 現在値: yfinance -> 直近の約定 -> avg_cost の順でフォールバック
    - 勝率: 含み益の銘柄割合（現物のみ）
    - セクター: broker 別で集計
    """
    # 価格取得（重複排除）
    tickers = sorted({(h.ticker or "").upper().strip() for h in Holding.objects.all() if h.ticker})
    price_map = get_prices(tickers) if tickers else {}

    total_mv_spot = 0.0
    total_cost_spot = 0.0
    total_mv_margin = 0.0
    total_cost_margin = 0.0

    winners = 0
    total_rows = 0

    sector_map: Dict[str, Dict[str, float]] = defaultdict(lambda: {"mv": 0.0, "cost": 0.0})

    for h in Holding.objects.all():
        qty = _to_float(h.quantity)
        avg_cost = _to_float(h.avg_cost)
        t = (h.ticker or "").upper().strip()

        # 現在値のフォールバック順
        current_price = _to_float(price_map.get(t, None))
        if current_price <= 0:
            # 直近約定は price_provider 側で試行済み。最後の砦として avg_cost
            current_price = avg_cost

        mv = current_price * qty
        cost = avg_cost * qty

        if h.account == "MARGIN":
            total_mv_margin += mv
            total_cost_margin += cost
        else:
            total_mv_spot += mv
            total_cost_spot += cost
            total_rows += 1
            if current_price > avg_cost:
                winners += 1

            sec_key = h.broker or "OTHER"
            sector_map[sec_key]["mv"] += mv
            sector_map[sec_key]["cost"] += cost

    pnl_spot = total_mv_spot - total_cost_spot
    win_ratio = round((winners / total_rows * 100) if total_rows else 0.0, 2)

    by_sector: List[Dict[str, Any]] = []
    for sec, d in sector_map.items():
        mv = d["mv"]
        cost = d["cost"]
        rate = round(((mv - cost) / cost * 100) if cost > 0 else 0.0, 2)
        by_sector.append({"sector": sec, "mv": round(mv), "rate": rate})
    by_sector.sort(key=lambda x: x["mv"], reverse=True)

    return dict(
        total_mv=round(total_mv_spot),         # 総資産は現物のみ
        total_cost=round(total_cost_spot),
        pnl=round(pnl_spot),                   # 含み損益（現物）
        win_ratio=win_ratio,                   # 勝率（現物）
        by_sector=by_sector[:10],
        breakdown={                            # 内訳カード用
            "spot_mv": round(total_mv_spot),
            "spot_cost": round(total_cost_spot),
            "margin_mv": round(total_mv_margin),
            "margin_cost": round(total_cost_margin),
        },
    )


# =========================
# 実現損益（月次）
# =========================
def _sum_realized_month() -> int:
    first, next_first = _month_bounds()
    total = 0.0
    for r in RealizedTrade.objects.filter(trade_at__gte=first, trade_at__lt=next_first):
        total += _to_float(r.pnl)
    return round(total)


# =========================
# 配当（月次）
# =========================
def _sum_dividend_month() -> int:
    first, next_first = _month_bounds()
    total = 0.0
    for d in Dividend.objects.filter(date__gte=first, date__lt=next_first):
        total += _to_float(d.amount)
    return round(total)


# =========================
# 現金（Cashモデル未導入のため0）
# =========================
def _cash_balance() -> int:
    return 0


# =========================
# キャッシュフロー（今月）
# =========================
def _cashflow_month_bars() -> list:
    return [
        {"label": "配当", "value": _sum_dividend_month()},
        {"label": "実現益", "value": _sum_realized_month()},
    ]


# =========================
# ストレステスト（現物総資産に対して）
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
        "total_assets": snap["total_mv"],         # 現物のみ
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
        breakdown=snap["breakdown"],  # 現物/信用の内訳（テンプレで表示）
    )
    return render(request, "home.html", ctx)