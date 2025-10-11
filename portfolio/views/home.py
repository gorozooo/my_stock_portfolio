# portfolio/views/home.py
from __future__ import annotations
from datetime import date, timedelta
from collections import defaultdict
from typing import Dict, List, Any, Union, Optional, Tuple
from decimal import Decimal
from django.shortcuts import render

from ..services.model_resolver import resolve_models
from ..services import advisor as svc_advisor

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

def _get_attr_safe(obj: Any, field_name: Optional[str], default: float = 0.0) -> float:
    """field_name が None でも安全に既定値を返す"""
    if not field_name:
        return default
    return _to_float(getattr(obj, field_name, default))

# =========================
# スナップショット（自動検出版・安全版）
# =========================
def _holdings_snapshot() -> dict:
    resolved = resolve_models("portfolio")
    hold = resolved.get("holding")
    if not hold:
        return dict(total_mv=0, total_cost=0, pnl=0, win_ratio=0, by_sector=[])

    M = hold["model"]
    price_f: str = hold.get("price")                  # 価格（必須想定）
    unit_f: Optional[str] = hold.get("unit")          # 取得単価（無い場合あり）
    shares_f: str = hold.get("shares")                # 株数
    sector_f: Optional[str] = hold.get("sector")      # セクター（無くてもOK）

    total_mv = 0.0
    total_cost = 0.0
    winners = 0
    total_rows = 0

    sector_map: Dict[str, Dict[str, float]] = defaultdict(lambda: {"mv": 0.0, "cost": 0.0})

    for h in M.objects.all():
        price = _get_attr_safe(h, price_f, 0.0)
        # unit_f が無ければ “取得単価＝現在値” とみなす（まず落ちないことを優先）
        unit = _get_attr_safe(h, unit_f, price)
        shares = _get_attr_safe(h, shares_f, 0.0)
        sec = str(getattr(h, sector_f, "その他")) if sector_f else "その他"

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
    win_ratio = round((winners / total_rows * 100) if total_rows else 0.0, 2)

    by_sector: List[Dict[str, Any]] = []
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

def _month_bounds(today: Optional[date] = None) -> Tuple[date, date]:
    """当月の [first, next_first) を返す（31日問題を回避）"""
    d = today or date.today()
    first = d.replace(day=1)
    next_first = (first.replace(day=28) + timedelta(days=4)).replace(day=1)
    return first, next_first

def _guess_date_field(M) -> Optional[str]:
    """モデルから“日付っぽい”フィールド名を推測"""
    cands = [
        "date", "received_date", "close_date", "pay_date", "trade_date",
        "settled_at", "updated_at", "created_at", "year",
    ]
    fields = {f.name for f in M._meta.get_fields() if hasattr(f, "name")}
    for c in cands:
        if c in fields:
            return c
    return None

def _sum_month(model_info: Optional[dict], date_field: Optional[str], amount_field: str) -> int:
    """
    当月の合計を計算。日付フィールドが分からない/型が不整合でも落ちない。
    - date_field が None のときは _guess_date_field で推測
    - フィルタに失敗したら全件合算にフォールバック
    """
    if not model_info:
        return 0
    M = model_info["model"]
    amt_f = amount_field
    dt_f = date_field or _guess_date_field(M)
    total = 0.0

    if dt_f:
        first, next_first = _month_bounds()
        try:
            for obj in M.objects.filter(**{f"{dt_f}__gte": first, f"{dt_f}__lt": next_first}):
                total += _to_float(getattr(obj, amt_f, 0.0))
            return round(total)
        except Exception:
            # 例：year(int) しか無い、型が違う…などは全件合算へ
            pass

    for obj in M.objects.all():
        total += _to_float(getattr(obj, amt_f, 0.0))
    return round(total)

def _cash_balance(resolved: dict) -> int:
    info = resolved.get("cash")
    if not info:
        return 0
    M = info["model"]
    amt_f = info["amount"]
    total = 0.0
    for c in M.objects.all():
        total += _to_float(getattr(c, amt_f, 0.0))
    return round(total)

def _cashflow_month_bars(resolved: dict) -> list:
    div = resolved.get("dividend")
    real = resolved.get("realized")
    return [
        {"label": "配当", "value": _sum_month(div, (div or {}).get("date"), (div or {}).get("amount", "amount")) if div else 0},
        {"label": "実現益", "value": _sum_month(real, (real or {}).get("date"), (real or {}).get("amount", "profit")) if real else 0},
    ]

def _stress_test(delta_index_pct: float, snapshot: dict) -> int:
    beta = 0.9
    return round(snapshot["total_mv"] * (1.0 + beta * delta_index_pct / 100.0))

# =========================
# View
# =========================
def home(request):
    resolved = resolve_models("portfolio")
    snap = _holdings_snapshot()

    realized_info = resolved.get("realized") or {}
    realized_month = _sum_month(realized_info, realized_info.get("date"), realized_info.get("amount", "profit")) if realized_info else 0

    kpis = {
        "total_assets": snap["total_mv"],
        "unrealized_pnl": snap["pnl"],
        "realized_month": realized_month,
        "win_ratio": snap["win_ratio"],
        "cash_balance": _cash_balance(resolved),
    }

    sectors = snap["by_sector"]
    cash_bars = _cashflow_month_bars(resolved)

    # AIコメント（スタブ）
    ai_note, ai_actions = svc_advisor.summarize(kpis, sectors)

    stressed = _stress_test(-5, snap)

    ctx = dict(
        kpis=kpis,
        sectors=sectors,
        cash_bars=cash_bars,
        ai_note=ai_note,
        ai_actions=ai_actions,
        stressed_default=stressed,
        _resolved_summary={
            k: {"model": v["model"].__name__, **{kk: v.get(kk) for kk in v if kk != "model"}}
            for k, v in resolved.items()
        },
    )
    return render(request, "home.html", ctx)