# -*- coding: utf-8 -*-
from __future__ import annotations
from datetime import date, timedelta
from collections import defaultdict
from typing import Dict, List, Any, Union, Optional, Tuple
from decimal import Decimal
import random

from django.shortcuts import render
from django.db.models import Sum
from django.http import HttpResponse

from ..services import advisor as svc_advisor
from ..services.insights import generate_insights   # ← 追加：AIインサイト生成
from ..models import Holding, RealizedTrade
from ..models_cash import BrokerAccount, CashLedger

Number = Union[int, float, Decimal]

# ========= A/B: variant 決定（A=ルール / B=policy補正） =========
def _pick_variant(request) -> str:
    # 既にクッキーがあればそれを使用
    v = (request.COOKIES.get("ab_variant") or "").upper()
    if v in ("A", "B"):
        return v
    # なければ 50/50 でランダムに決定
    return "A" if random.random() < 0.5 else "B"

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

# ========= Holdings / Sector snapshot =========
def _holdings_snapshot() -> dict:
    holdings = list(Holding.objects.all())

    spot_mv = spot_cost = 0.0
    margin_mv = margin_cost = 0.0
    sector_map: Dict[str, Dict[str, float]] = defaultdict(lambda: {"mv": 0.0, "cost": 0.0})

    for h in holdings:
        qty = _to_float(getattr(h, "quantity", 0))
        unit = _to_float(getattr(h, "avg_cost", 0))
        price = _to_float(getattr(h, "last_price", None)) or unit
        mv = price * qty
        cost = unit * qty

        acc = (getattr(h, "account", "") or "").upper()
        sector = (getattr(h, "sector", None) or "").strip() or "未分類"

        if acc == "MARGIN":
            margin_mv += mv
            margin_cost += cost
        else:
            spot_mv += mv
            spot_cost += cost

        sector_map[sector]["mv"] += mv
        sector_map[sector]["cost"] += cost

    total_unrealized_pnl = (spot_mv + margin_mv) - (spot_cost + margin_cost)

    qs = RealizedTrade.objects.all()
    win = sum(1 for r in qs if _to_float(getattr(r, "pnl", 0)) > 0)
    lose = sum(1 for r in qs if _to_float(getattr(r, "pnl", 0)) < 0)
    total_trades = win + lose
    win_ratio = round((win / total_trades * 100.0) if total_trades else 0.0, 1)

    by_sector: List[Dict[str, Any]] = []
    for sec, d in sector_map.items():
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

# ========= Realized / Dividend =========
def _sum_realized_month() -> int:
    first, next_first = _month_bounds()
    qs = CashLedger.objects.filter(
        source_type=CashLedger.SourceType.REALIZED,
        at__gte=first, at__lt=next_first,
    )
    return int(sum(int(x.amount) for x in qs))

def _sum_dividend_month() -> int:
    first, next_first = _month_bounds()
    qs = CashLedger.objects.filter(
        source_type=CashLedger.SourceType.DIVIDEND,
        at__gte=first, at__lt=next_first,
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
    dep = int(CashLedger.objects.filter(kind=CashLedger.Kind.DEPOSIT).aggregate(s=Sum("amount")).get("s") or 0)
    xin = int(CashLedger.objects.filter(kind=CashLedger.Kind.XFER_IN).aggregate(s=Sum("amount")).get("s") or 0)
    wdr = int(CashLedger.objects.filter(kind=CashLedger.Kind.WITHDRAW).aggregate(s=Sum("amount")).get("s") or 0)
    xout= int(CashLedger.objects.filter(kind=CashLedger.Kind.XFER_OUT).aggregate(s=Sum("amount")).get("s") or 0)
    return int(opening + dep + xin - wdr - xout)

# ========= Stress (β=0.9) =========
def _stress_total_assets(pct: float, snap: dict, cash_total: int) -> int:
    beta = 0.9
    equity_mv = snap["spot_mv"] + snap["margin_mv"]
    stressed_equity = equity_mv * (1.0 + beta * pct / 100.0)
    return int(round(stressed_equity + cash_total))

# ========= View =========
def home(request):
    # ---- A/B variant 決定 ----
    variant = _pick_variant(request)

    snap = _holdings_snapshot()
    cash = _cash_balances()

    total_eval_assets = int(snap["spot_mv"] + snap["margin_mv"] + cash["total"])
    unrealized_pnl = int(snap["unrealized"])

    realized_month = _sum_realized_month()
    dividend_month = _sum_dividend_month()
    realized_cum = _sum_realized_cum()
    dividend_cum = _sum_dividend_cum()

    margin_unrealized = int(snap["margin_mv"] - snap["margin_cost"])
    liquidation_value = int(snap["spot_mv"] + margin_unrealized + cash["total"])

    invested = _invested_capital()

    roi_eval_pct = round(((total_eval_assets - invested) / invested * 100.0), 2) if invested > 0 else None
    roi_liquid_pct = round(((liquidation_value - invested) / invested * 100.0), 2) if invested > 0 else None
    roi_gap_abs = round(abs(roi_eval_pct - roi_liquid_pct), 2) if (roi_eval_pct is not None and roi_liquid_pct is not None) else None

    gross_pos = max(int(snap["spot_mv"] + snap["margin_mv"]), 1)
    breakdown_pct = {
        "spot_pct": round(snap["spot_mv"] / gross_pos * 100, 1),
        "margin_pct": round(snap["margin_mv"] / gross_pos * 100, 1),
    }
    liquidity_rate_pct = max(0.0, round(liquidation_value / total_eval_assets * 100, 1)) if total_eval_assets > 0 else 0.0
    margin_ratio_pct = round(snap["margin_mv"] / gross_pos * 100, 1) if gross_pos > 0 else 0.0

    risk_flags: List[str] = []
    if margin_ratio_pct >= 60.0:
        risk_flags.append(f"信用比率が {margin_ratio_pct}%（60%超）です。余力とボラに注意。")
    if liquidity_rate_pct < 50.0:
        risk_flags.append(f"流動性が {liquidity_rate_pct}% と低め。現金化余地の確保を検討。")

    kpis = {
        "total_assets": total_eval_assets,
        "unrealized_pnl": unrealized_pnl,
        "realized_month": realized_month,
        "dividend_month": dividend_month,
        "realized_cum": realized_cum,
        "dividend_cum": dividend_cum,
        "cash_total": cash["total"],
        "liquidation": liquidation_value,
        "invested": invested,
        "roi_eval_pct": roi_eval_pct,
        "roi_liquid_pct": roi_liquid_pct,
        "roi_gap_abs": roi_gap_abs,
        "win_ratio": snap["win_ratio"],
        "liquidity_rate_pct": liquidity_rate_pct,
        "margin_ratio_pct": margin_ratio_pct,
        "margin_unrealized": margin_unrealized,
        # ▼▼ セッションにも残すため variant を入れておく ▼▼
        "ab_variant": variant,
    }

    sectors = snap["by_sector"]
    cash_bars = [
        {"label": "配当", "value": dividend_month},
        {"label": "実現益", "value": realized_month},
    ]

    # === AI生成（variant を渡すと B 側は policy 補正が効く実装にしてある前提）===
    ai_note, ai_items, ai_session_id, weekly_draft, nextmove_draft = svc_advisor.summarize(
        kpis, sectors, variant=variant
    )

    if not ai_note:
        ai_note = "最新データを解析しました。主要KPIと含み状況を要約しています。"
    if not ai_items:
        ai_items = [dict(id=0, message="直近のデータが少ないため、提案事項はありません。", score=0.0, taken=False, kind="REBALANCE")]

    # ROI乖離を先頭へ優先表示
    if kpis.get("roi_gap_abs") is not None and kpis["roi_gap_abs"] >= 20:
        key = "評価ROIと現金ROIの乖離が"
        idx = next((i for i, x in enumerate(ai_items) if key in x["message"]), None)
        if idx not in (None, 0):
            ai_items.insert(0, ai_items.pop(idx))

    # === ★ セッション永続化（variant も保存） ===
    try:
        ai_items = svc_advisor.ensure_session_persisted(ai_note, ai_items, kpis, variant=variant)
    except Exception as e:
        print(f"[WARN] advisor session save failed: {e}")

    # 🧠 追加：AIインサイト生成（未定義エラー対策）
    ins_title, ins_bullets = generate_insights(horizon_days=7, since_days=90, top_k=3)

    stressed_default = _stress_total_assets(-5.0, snap, cash["total"])

    # --- レスポンス + クッキー設定 ---
    ctx = dict(
        kpis=kpis,
        sectors=sectors,
        cash_bars=cash_bars,
        breakdown=dict(
            spot_mv=snap["spot_mv"],
            spot_cost=snap["spot_cost"],
            margin_mv=snap["margin_mv"],
            margin_cost=snap["margin_cost"],
        ),
        breakdown_pct=breakdown_pct,
        risk_flags=risk_flags,
        cash_total_by_currency=cash["total_by_currency"],
        stressed_default=stressed_default,
        ai_note=ai_note,
        ai_items=ai_items,
        ai_session_id=ai_session_id,
        weekly_draft=weekly_draft,
        nextmove_draft=nextmove_draft,
        ai_insights_title=ins_title,      # ← 追加
        ai_insights_bullets=ins_bullets,  # ← 追加
    )
    resp: HttpResponse = render(request, "home.html", ctx)
    # 7日間クッキー
    resp.set_cookie("ab_variant", variant, max_age=7*24*60*60, samesite="Lax")
    return resp