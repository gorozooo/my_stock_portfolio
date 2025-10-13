# portfolio/views/home.py
from __future__ import annotations
from datetime import date, timedelta
from collections import defaultdict
from typing import Dict, List, Any, Union, Optional, Tuple
from decimal import Decimal

from django.shortcuts import render

from ..services import advisor as svc_advisor
from ..models import Holding, RealizedTrade, Dividend

Number = Union[int, float, Decimal]


# =========================
# Utilities
# =========================
def _to_float(v: Optional[Number]) -> float:
    """数値→float（None/Decimal/例外に強い）"""
    try:
        if v is None: return 0.0
        if isinstance(v, Decimal): return float(v)
        return float(v)
    except Exception:
        return 0.0


def _month_bounds(today: Optional[date] = None) -> Tuple[date, date]:
    """当月 [first, next_first) を返す（28日トリックで安全）"""
    d = today or date.today()
    first = d.replace(day=1)
    next_first = (first.replace(day=28) + timedelta(days=4)).replace(day=1)
    return first, next_first


# =========================
# Snapshots（DBの last_price を最優先に使用）
# =========================
def _holdings_snapshot() -> dict:
    """
    - 総資産は現物のみ（account != 'MARGIN'）
    - 価格は DBの last_price を最優先 → 無ければ avg_cost（最後の砦）
    - 含み損益は「現物＋信用」の **未実現損益のみ**（= 評価額−取得額）
      ※ 実現損益・配当・手数料・税金は含めない
    - 勝率は全期間の実現トレード（pnl>0 勝ち / <0 負け）
    - セクター（暫定）：broker 別（現物のみ集計）
    """
    holdings = list(Holding.objects.all())

    # 合計器
    spot_mv = spot_cost = 0.0
    margin_mv = margin_cost = 0.0

    sector_map: Dict[str, Dict[str, float]] = defaultdict(lambda: {"mv": 0.0, "cost": 0.0})

    for h in holdings:
        qty  = _to_float(h.quantity)
        unit = _to_float(h.avg_cost)
        price = _to_float(getattr(h, "last_price", None)) or unit  # バッチ値→無ければ取得単価
        mv = price * qty
        cost = unit * qty

        if (h.account or "").upper() == "MARGIN":
            margin_mv  += mv
            margin_cost+= cost
        else:
            spot_mv  += mv
            spot_cost+= cost
            # セクター（=証券会社での内訳表示）
            sec_key = h.broker or "OTHER"
            sector_map[sec_key]["mv"]   += mv
            sector_map[sec_key]["cost"] += cost

    # ★ 未実現損益 = (現物+信用の評価額) − (現物+信用の取得額)
    total_unrealized_pnl = (spot_mv + margin_mv) - (spot_cost + margin_cost)

    # 勝率＝全期間の実現トレード（未実現とは別系統）
    qs = RealizedTrade.objects.all()
    win = sum(1 for r in qs if _to_float(r.pnl) > 0)
    lose = sum(1 for r in qs if _to_float(r.pnl) < 0)
    total_trades = win + lose
    win_ratio = round((win / total_trades * 100.0) if total_trades else 0.0, 1)

    # セクターカード（現物のみ）
    by_sector: List[Dict[str, Any]] = []
    for sec, d in sector_map.items():
        mv, cost = d["mv"], d["cost"]
        rate = round(((mv - cost) / cost * 100) if cost > 0 else 0.0, 2)
        by_sector.append({"sector": sec, "mv": round(mv), "rate": rate})
    by_sector.sort(key=lambda x: x["mv"], reverse=True)

    return dict(
        # 総資産は“現物のみ”の方針を維持（要件があれば切り替え可）
        total_mv=round(spot_mv),
        total_cost=round(spot_cost),
        # ← 表示カード用（参考値）。実計算は下の total_unrealized_pnl を使用
        pnl=round(total_unrealized_pnl),
        win_ratio=win_ratio,
        by_sector=by_sector[:10],
        breakdown={
            "spot_mv": round(spot_mv),
            "spot_cost": round(spot_cost),
            "margin_mv": round(margin_mv),
            "margin_cost": round(margin_cost),
        },
    )


# =========================
# KPI helpers（実現・配当は別KPI、未実現へは混ぜない）
# =========================
def _sum_realized_month() -> int:
    first, next_first = _month_bounds()
    total = 0.0
    for r in RealizedTrade.objects.filter(trade_at__gte=first, trade_at__lt=next_first):
        total += _to_float(r.pnl)
    return round(total)


def _sum_dividend_month() -> int:
    first, next_first = _month_bounds()
    total = 0.0
    for d in Dividend.objects.filter(date__gte=first, date__lt=next_first):
        total += _to_float(d.amount)
    return round(total)


def _cash_balance() -> int:
    return 0


def _cashflow_month_bars() -> list:
    return [
        {"label": "配当", "value": _sum_dividend_month()},
        {"label": "実現益", "value": _sum_realized_month()},
    ]


def _stress_test(delta_index_pct: float, snapshot: dict) -> int:
    beta = 0.9
    return round(snapshot["total_mv"] * (1.0 + beta * delta_index_pct / 100.0))


# =========================
# View
# =========================
def home(request):
    snap = _holdings_snapshot()

    # KPI（未実現＝現物＋信用）
    kpis = {
        "total_assets":   snap["total_mv"],     # 現物のみ（方針）
        "unrealized_pnl": snap["pnl"],         # ★ 現物＋信用の未実現損益
        "realized_month": _sum_realized_month(),
        "win_ratio":      snap["win_ratio"],   # 実現トレードの勝率（全期間）
        "cash_balance":   _cash_balance(),
    }

    # 内訳の比率（ゲージ用）
    spot = snap["breakdown"]["spot_mv"]
    margin = snap["breakdown"]["margin_mv"]
    denom = max(spot + margin, 1)  # 0割回避
    breakdown_pct = {
        "spot_pct": round(spot / denom * 100, 1),
        "margin_pct": round(margin / denom * 100, 1),
    }

    sectors   = snap["by_sector"]         # 現物のみのセクター成績
    cash_bars = _cashflow_month_bars()
    ai_note, ai_actions = svc_advisor.summarize(kpis, sectors)
    stressed = _stress_test(-5, snap)

    ctx = dict(
        kpis=kpis,
        sectors=sectors,
        cash_bars=cash_bars,
        ai_note=ai_note, ai_actions=ai_actions,
        stressed_default=stressed,
        breakdown=snap["breakdown"],      # 数値カード
        breakdown_pct=breakdown_pct,      # 比率ゲージ
    )
    return render(request, "home.html", ctx)