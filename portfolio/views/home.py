# portfolio/views/home.py
from __future__ import annotations
from datetime import date, timedelta
from collections import defaultdict
from typing import Dict, List, Any, Union, Optional, Tuple
from decimal import Decimal

from django.shortcuts import render
from django.db.models import Sum  # ★ 集計で使用

from ..services import advisor as svc_advisor
from ..models import Holding, RealizedTrade, Dividend
from ..models_cash import BrokerAccount, CashLedger

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

# =========================
# Cash（BrokerAccount + CashLedger）
# =========================
def _cash_balances() -> Dict[str, Any]:
    """
    口座現金の合計と、証券会社別の内訳を返す。
    opening_balance + ledgers(増減) の純額。通貨はとりあえず JPY のみ合算。
    戻り値:
      {
        "total": int,
        "by_broker": [{"broker":"SBI","cash":..., "currency":"JPY"}, ...],
        "total_by_currency": {"JPY": ...}
      }
    """
    accounts = list(BrokerAccount.objects.all().prefetch_related("ledgers"))

    cur_totals: Dict[str, int] = defaultdict(int)
    by_broker: Dict[str, int] = defaultdict(int)

    for a in accounts:
        # Djangoの集計で安全に合計
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

# =========================
# Holdings（現物＋信用の評価 / 取得 / セクター）
# =========================
def _holdings_snapshot() -> dict:
    """
    - 価格は DB の last_price 最優先 → 無ければ avg_cost（最後の砦）
    - 含み損益は「現物＋信用」の未実現のみ（評価−取得）
    - 総資産（評価ベース）は現物＋信用の評価額 + 現金（別計）
    - セクター相当は broker 別で現物のみ集計（UIの簡潔さ優先）
    """
    holdings = list(Holding.objects.all())

    spot_mv = spot_cost = 0.0
    margin_mv = margin_cost = 0.0

    # broker 別（現物のみ）内訳
    broker_map: Dict[str, Dict[str, float]] = defaultdict(lambda: {"mv": 0.0, "cost": 0.0})
    # broker 別（現物＋信用）評価（口座ビュー用）
    broker_pos_mv: Dict[str, float] = defaultdict(float)

    for h in holdings:
        qty = _to_float(getattr(h, "quantity", 0))
        unit = _to_float(getattr(h, "avg_cost", 0))
        price = _to_float(getattr(h, "last_price", None)) or unit  # last_price 優先
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

        broker_pos_mv[broker] += mv  # 現物＋信用の合計評価

    # 未実現（現物＋信用）
    total_unrealized_pnl = (spot_mv + margin_mv) - (spot_cost + margin_cost)

    # 勝率＝実現トレード全期間
    qs = RealizedTrade.objects.all()
    win = sum(1 for r in qs if _to_float(getattr(r, "pnl", 0)) > 0)
    lose = sum(1 for r in qs if _to_float(getattr(r, "pnl", 0)) < 0)
    total_trades = win + lose
    win_ratio = round((win / total_trades * 100.0) if total_trades else 0.0, 1)

    # セクター（= broker）カード（現物）
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
        by_broker_pos_mv={k: round(v) for k, v in broker_pos_mv.items()},
    )

# =========================
# Realized / Dividend / ROI
# =========================
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
    qs = CashLedger.objects.filter(source_type=CashLedger.SourceType.REALIZED)
    return int(sum(int(x.amount) for x in qs))

def _sum_dividend_cum() -> int:
    qs = CashLedger.objects.filter(source_type=CashLedger.SourceType.DIVIDEND)
    return int(sum(int(x.amount) for x in qs))

def _invested_capital() -> int:
    """
    投下元本（正味の入出金）= opening_balance 合計 + (DEPOSIT+XFER_IN) - (WITHDRAW+XFER_OUT)
    """
    opening = int(
        BrokerAccount.objects.aggregate(total=Sum("opening_balance")).get("total") or 0
    )
    dep = int(CashLedger.objects.filter(kind=CashLedger.Kind.DEPOSIT).aggregate(s=Sum("amount")).get("s") or 0)
    xin = int(CashLedger.objects.filter(kind=CashLedger.Kind.XFER_IN).aggregate(s=Sum("amount")).get("s") or 0)
    wdr = int(CashLedger.objects.filter(kind=CashLedger.Kind.WITHDRAW).aggregate(s=Sum("amount")).get("s") or 0)
    xout= int(CashLedger.objects.filter(kind=CashLedger.Kind.XFER_OUT).aggregate(s=Sum("amount")).get("s") or 0)
    return int(opening + dep + xin - wdr - xout)

# =========================
# View
# =========================
def home(request):
    snap = _holdings_snapshot()
    cash = _cash_balances()

    # 評価ベースの総資産（実質）= (現物+信用)評価額 + 現金
    total_eval_assets = int(snap["spot_mv"] + snap["margin_mv"] + cash["total"])

    # KPI: 未実現（現物＋信用）
    unrealized_pnl = int(snap["unrealized"])

    # 実現 & 配当
    realized_month = _sum_realized_month()
    dividend_month = _sum_dividend_month()
    realized_cum   = _sum_realized_cum()
    dividend_cum   = _sum_dividend_cum()

    # Liquidation（今すぐ全て現金化）
    liquidation_value = int(snap["spot_mv"] + snap["margin_mv"] + cash["total"])

    # ROI（投下資金比）
    invested = _invested_capital()
    roi_pct = round(((liquidation_value - invested) / invested * 100.0), 2) if invested > 0 else None

    # 現物/信用の比率（ゲージ用）
    denom_pos = max(int(snap["spot_mv"] + snap["margin_mv"]), 1)
    breakdown_pct = {
        "spot_pct": round(snap["spot_mv"] / denom_pos * 100, 1),
        "margin_pct": round(snap["margin_mv"] / denom_pos * 100, 1),
    }

    # 証券会社別：現金＋ポジション評価
    broker_rows: List[Dict[str, Any]] = []
    cash_by_broker = {b["broker"]: b["cash"] for b in cash["by_broker"]}
    pos_by_broker = snap["by_broker_pos_mv"]
    all_brokers = set(cash_by_broker.keys()) | set(pos_by_broker.keys())
    for b in sorted(all_brokers):
        cash_jpy = int(cash_by_broker.get(b, 0))
        pos_mv   = int(pos_by_broker.get(b, 0))
        broker_rows.append({
            "broker": b, "cash": cash_jpy, "pos_mv": pos_mv, "total": cash_jpy + pos_mv
        })
    broker_rows.sort(key=lambda r: r["total"], reverse=True)

    # 画面トップKPI（“総資産”は実質評価ベース）
    kpis = {
        "total_assets": total_eval_assets,     # = (現物+信用)評価額 + 現金
        "unrealized_pnl": unrealized_pnl,      # 未実現（現物+信用）
        "realized_month": realized_month,      # 今月の実現損益（現金ベース）
        "dividend_month": dividend_month,      # 今月の配当（現金ベース）
        "cash_total": cash["total"],           # 現金残高合計
        "liquidation": liquidation_value,      # 今すぐ現金化
        "invested": invested,                  # 投下元本（正味入出金）
        "roi_pct": roi_pct,                    # ROI%
        "win_ratio": snap["win_ratio"],        # 実現トレード勝率（参考）
        # 参考：累計
        "realized_cum": realized_cum,
        "dividend_cum": dividend_cum,
    }

    # セクター（=broker）カードは現物のみの成績
    sectors = snap["by_sector"]

    # キャッシュフロー棒（今月）
    cash_bars = [
        {"label":"配当", "value": dividend_month},
        {"label":"実現益", "value": realized_month},
    ]

    ai_note, ai_actions = svc_advisor.summarize(kpis, sectors)

    ctx = dict(
        kpis=kpis,
        sectors=sectors,
        cash_bars=cash_bars,
        breakdown=dict(
            spot_mv=snap["spot_mv"], spot_cost=snap["spot_cost"],
            margin_mv=snap["margin_mv"], margin_cost=snap["margin_cost"]
        ),
        breakdown_pct=breakdown_pct,
        broker_rows=broker_rows,
        # 補助（テンプレ/デバッグ）
        cash_total_by_currency=cash["total_by_currency"],
    )
    return render(request, "home.html", ctx)