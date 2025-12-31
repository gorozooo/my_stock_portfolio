# portfolio/services/home_assets.py
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Tuple

from django.db.models import Count, F, Sum, Value, Case, When, ExpressionWrapper, DecimalField
from django.db.models.functions import Coalesce
from django.utils import timezone

from ..models import RealizedTrade, UserSetting


BROKER_LABEL = {
    "RAKUTEN": "楽天証券",
    "SBI": "SBI証券",
    "MATSUI": "松井証券",
    "OTHER": "その他",
}

DEC2 = DecimalField(max_digits=20, decimal_places=2)
DEC4 = DecimalField(max_digits=20, decimal_places=4)


def _today() -> date:
    try:
        return timezone.localdate()
    except Exception:
        return timezone.now().date()


def _year_start(d: date) -> date:
    return date(d.year, 1, 1)


def _month_start(d: date) -> date:
    return date(d.year, d.month, 1)


def _week_start_monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _remaining_months_including_current(d: date) -> int:
    return (12 - d.month) + 1


def _remaining_weeks_including_current(d: date) -> int:
    iso_week = int(d.isocalendar().week)
    return max(1, 52 - iso_week + 1)


def _d0(x) -> Decimal:
    try:
        if x is None:
            return Decimal("0")
        return Decimal(str(x))
    except Exception:
        return Decimal("0")


# ============================================================
#  realized.py と同一の「円換算PnL」(pnl_jpy_calc) を付与
#   - pnl_display = cashflow（= 投資家PnL / 手入力）
#   - fx_to_jpy_calc:
#       USD & fx_rate>0 → fx_rate
#       JPY or それ以外 → 1
#   - pnl_jpy_calc = pnl_display * fx_to_jpy_calc
# ============================================================
def _annotate_pnl_jpy_like_realized(qs):
    dec0 = Value(Decimal("0"), output_field=DEC2)
    one = Value(Decimal("1"), output_field=DEC4)

    # 投資家PnL（通貨建て）
    pnl_display = Coalesce(F("cashflow"), dec0)

    # 1通貨あたり何円か（realized.py と同じ判定）
    fx_to_jpy_calc = Case(
        When(
            currency__iexact="USD",
            fx_rate__isnull=False,
            fx_rate__gt=0,
            then=F("fx_rate"),
        ),
        When(currency__iexact="JPY", then=one),
        default=one,
        output_field=DEC4,
    )

    pnl_jpy_calc = ExpressionWrapper(pnl_display * fx_to_jpy_calc, output_field=DEC2)

    return qs.annotate(
        pnl_display=ExpressionWrapper(pnl_display, output_field=DEC2),
        fx_to_jpy_calc=fx_to_jpy_calc,
        pnl_jpy_calc=pnl_jpy_calc,
    )


def _sum_pnl_sell(
    user,
    start: date,
    end: date | None = None,
    broker: str | None = None,
) -> Tuple[Decimal, int]:
    """
    ✅ realized.py と一致させる
    - RealizedTrade の SELL のみ
    - 期間は trade_at
    - PnL は「pnl_jpy_calc（= cashflow の円換算）」を合計
    """
    qs = RealizedTrade.objects.filter(user=user, side="SELL", trade_at__gte=start)
    if end is not None:
        qs = qs.filter(trade_at__lte=end)
    if broker:
        qs = qs.filter(broker=broker)

    qs = _annotate_pnl_jpy_like_realized(qs)

    agg = qs.aggregate(
        total=Coalesce(Sum("pnl_jpy_calc", output_field=DEC2), Value(Decimal("0"), output_field=DEC2)),
        cnt=Coalesce(Count("id"), Value(0)),
    )
    return _d0(agg.get("total")), int(agg.get("cnt") or 0)


def _broker_rows_ytd(user, start: date) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for key in ("RAKUTEN", "SBI", "MATSUI", "OTHER"):
        total, cnt = _sum_pnl_sell(user, start=start, broker=key)
        rows.append(
            {
                "broker": key,
                "label": BROKER_LABEL.get(key, key),
                "ytd": float(total),
                "count": cnt,
            }
        )
    return rows


def _build_pace(
    goal_year: Decimal,
    ytd: Decimal,
    d: date,
    by_broker_rows: List[Dict[str, Any]],
) -> Dict[str, Any]:
    remain = goal_year - ytd
    rem_m = _remaining_months_including_current(d)
    rem_w = _remaining_weeks_including_current(d)

    need_m = remain / Decimal(rem_m) if rem_m > 0 else remain
    need_w = remain / Decimal(rem_w) if rem_w > 0 else remain

    # broker別：今年目標は “全体目標をytd比で按分”（暫定）
    total_abs = sum(abs(r["ytd"]) for r in by_broker_rows) or 1.0

    by_rows = []
    for r in by_broker_rows:
        weight = abs(r["ytd"]) / total_abs
        goal_b = Decimal(goal_year) * Decimal(str(weight))
        remain_b = goal_b - Decimal(str(r["ytd"]))
        need_m_b = remain_b / Decimal(rem_m) if rem_m > 0 else remain_b

        by_rows.append(
            {
                "broker": r["broker"],
                "label": r["label"],
                "ytd": r["ytd"],
                "goal_year": int(goal_b),
                "pace_month": {
                    "remaining": float(remain_b),
                    "need_per_slot": float(need_m_b),
                },
            }
        )

    return {
        "remaining_months_including_current": rem_m,
        "remaining_weeks_including_current": rem_w,
        "total_need_per_month": {"remaining": float(remain), "need_per_slot": float(need_m)},
        "total_need_per_week": {"remaining": float(remain), "need_per_slot": float(need_w)},
        "by_broker_rows": by_rows,
    }


def build_assets_snapshot(user) -> Dict[str, Any]:
    d = _today()

    ytd_start = _year_start(d)
    mtd_start = _month_start(d)
    wtd_start = _week_start_monday(d)

    ytd_total, ytd_cnt = _sum_pnl_sell(user, start=ytd_start)
    mtd_total, mtd_cnt = _sum_pnl_sell(user, start=mtd_start)
    wtd_total, wtd_cnt = _sum_pnl_sell(user, start=wtd_start)

    setting, _ = UserSetting.objects.get_or_create(user=user)
    goal_year_total = getattr(setting, "realized_goal_year", None) or Decimal("0")

    by_broker = _broker_rows_ytd(user, start=ytd_start)
    pace = _build_pace(goal_year_total, ytd_total, d, by_broker)

    return {
        "status": "ok",
        "as_of": timezone.now().isoformat(),
        "realized": {
            "ytd": {"total": float(ytd_total), "count": ytd_cnt},
            "mtd": {"total": float(mtd_total), "count": mtd_cnt},
            "wtd": {"total": float(wtd_total), "count": wtd_cnt},
        },
        "goals": {
            "year_total": int(goal_year_total),
        },
        "pace": pace,
    }