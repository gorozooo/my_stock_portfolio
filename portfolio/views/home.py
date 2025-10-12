# portfolio/views/home.py
from __future__ import annotations
from datetime import date, timedelta
from collections import defaultdict
from typing import Dict, List, Any, Union, Optional, Tuple
from decimal import Decimal

from django.shortcuts import render

import pandas as pd
import yfinance as yf

from ..services import advisor as svc_advisor
from ..services import trend as svc_trend  # ← 保有ページと同じ正規化ロジックを使う
from ..models import Holding, RealizedTrade, Dividend

Number = Union[int, float, Decimal]


# =========================
# Utilities
# =========================
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


def _norm_ticker(raw: str) -> str:
    """'7013' -> '7013.T' など、保有ページと同じ正規化を使用"""
    return svc_trend._normalize_ticker(str(raw or ""))


# =========================
# Prices (last close)
# =========================
def _last_close_map(tickers_raw: List[str]) -> Dict[str, float]:
    """
    ティッカー配列を受け取り、最終終値（1株）を返す。
    戻り値キーは“元のティッカー”（正規化前）に揃える。
    """
    # 元ティッカー -> 正規化
    base = [(t or "").strip().upper() for t in tickers_raw if t]
    if not base:
        return {}

    norm_map: Dict[str, str] = {t: _norm_ticker(t) for t in base}
    need = sorted(set(norm_map.values()))
    out_norm: Dict[str, float] = {}

    # 市況休場も考慮して日数は少し多め
    try:
        df = yf.download(
            tickers=need if len(need) > 1 else need[0],
            period="40d",
            interval="1d",
            auto_adjust=True,
            progress=False,
            group_by="ticker",
        )
    except Exception:
        df = None

    def _pick_last(nsym: str) -> Optional[float]:
        if df is None:
            return None
        try:
            if isinstance(df.columns, pd.MultiIndex):
                # (TICKER, FIELD) の MultiIndex
                if (nsym, "Close") in df.columns:
                    s = df[(nsym, "Close")]
                else:
                    try:
                        s = df.xs(nsym, axis=1)["Close"]  # type: ignore[index]
                    except Exception:
                        return None
            else:
                # 単一ティッカー
                s = df["Close"]  # type: ignore[index]
        except Exception:
            return None
        try:
            last = pd.Series(s).dropna().iloc[-1]  # type: ignore[arg-type]
            v = float(last)
            return v if v > 0 else None
        except Exception:
            return None

    for nsym in need:
        v = _pick_last(nsym)
        if v is not None:
            out_norm[nsym] = v

    # 正規化→元ティッカーへ戻す
    out: Dict[str, float] = {}
    for orig, nsym in norm_map.items():
        if nsym in out_norm:
            out[orig] = out_norm[nsym]
    return out


# =========================
# Snapshots
# =========================
def _holdings_snapshot() -> dict:
    """
    - 総資産は現物のみ（account != 'MARGIN'）
    - 現在値は yfinance の最終終値（取れなければ avg_cost）
    - 含み損益は現物合算、勝率は全期間の実現トレード
    - セクター（仮）：broker 別
    """
    holdings = list(Holding.objects.all())

    # 価格を一括取得（保有ページと同じ“終値ベース”）
    tickers = [h.ticker for h in holdings if h.ticker]
    price_map = _last_close_map(tickers) if tickers else {}

    total_mv_spot = 0.0
    total_cost_spot = 0.0
    total_mv_margin = 0.0
    total_cost_margin = 0.0

    sector_map: Dict[str, Dict[str, float]] = defaultdict(lambda: {"mv": 0.0, "cost": 0.0})

    for h in holdings:
        qty = _to_float(h.quantity)
        unit = _to_float(h.avg_cost)
        t = (h.ticker or "").upper().strip()
        price = _to_float(price_map.get(t)) or unit  # フォールバックは avg_cost

        mv = price * qty
        cost = unit * qty

        if (h.account or "").upper() == "MARGIN":
            total_mv_margin += mv
            total_cost_margin += cost
        else:
            total_mv_spot += mv
            total_cost_spot += cost

            sec_key = h.broker or "OTHER"
            sector_map[sec_key]["mv"] += mv
            sector_map[sec_key]["cost"] += cost

    pnl_spot = total_mv_spot - total_cost_spot

    # 勝率＝全期間の実現トレード
    qs = RealizedTrade.objects.all()
    win = sum(1 for r in qs if _to_float(r.pnl) > 0)
    lose = sum(1 for r in qs if _to_float(r.pnl) < 0)
    total_trades = win + lose
    win_ratio = round((win / total_trades * 100.0) if total_trades else 0.0, 1)

    # セクターカード
    by_sector: List[Dict[str, Any]] = []
    for sec, d in sector_map.items():
        mv = d["mv"]
        cost = d["cost"]
        rate = round(((mv - cost) / cost * 100) if cost > 0 else 0.0, 2)
        by_sector.append({"sector": sec, "mv": round(mv), "rate": rate})
    by_sector.sort(key=lambda x: x["mv"], reverse=True)

    return dict(
        total_mv=round(total_mv_spot),
        total_cost=round(total_cost_spot),
        pnl=round(pnl_spot),
        win_ratio=win_ratio,
        by_sector=by_sector[:10],
        breakdown={
            "spot_mv": round(total_mv_spot),
            "spot_cost": round(total_cost_spot),
            "margin_mv": round(total_mv_margin),
            "margin_cost": round(total_cost_margin),
        },
    )


# =========================
# KPI helpers
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
    # Cash モデル未導入のため 0
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

    kpis = {
        "total_assets": snap["total_mv"],        # 現物のみ
        "unrealized_pnl": snap["pnl"],          # 含み損益（終値ベース）
        "realized_month": _sum_realized_month(),
        "win_ratio": snap["win_ratio"],         # 実現トレード全期間
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
        breakdown=snap["breakdown"],
    )
    return render(request, "home.html", ctx)