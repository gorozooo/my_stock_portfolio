# portfolio/services/home_assets.py
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Tuple

from django.db.models import (
    Sum, Count, F, Value, Case, When, ExpressionWrapper, DecimalField
)
from django.db.models.functions import Coalesce
from django.utils import timezone

from ..models import RealizedTrade, UserSetting


# =========================
# 表示ラベル
# =========================
BROKER_LABEL = {
    "RAKUTEN": "楽天証券",
    "SBI": "SBI証券",
    "MATSUI": "松井証券",
    "OTHER": "その他",
}

DEC2 = DecimalField(max_digits=20, decimal_places=2)
DEC4 = DecimalField(max_digits=20, decimal_places=4)


# =========================
# 日付ユーティリティ
# =========================
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


# =========================
# realized.py と同一の円換算PnL
# =========================
def _with_pnl_jpy(qs):
    """
    realized.py の pnl_jpy_calc と同義：
      pnl_jpy_calc = cashflow * fx_to_jpy
      - USD: fx_rate があればそれ
      - JPY: 1
    """
    dec0 = Value(Decimal("0"), output_field=DEC2)
    one = Value(Decimal("1"), output_field=DEC4)

    cashflow = Coalesce(F("cashflow"), dec0)

    fx_to_jpy = Case(
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

    pnl_jpy_calc = ExpressionWrapper(
        cashflow * fx_to_jpy,
        output_field=DEC2,
    )

    return qs.annotate(pnl_jpy_calc=pnl_jpy_calc)


# =========================
# 集計コア（realized と一致）
# =========================
def _sum_pnl_all(
    user,
    start: date,
    end: date | None = None,
    broker: str | None = None,
) -> Tuple[Decimal, int]:
    """
    - BUY / SELL 両方含める
    - 金額 = 円換算済み pnl_jpy_calc の合計
    - 件数 = Count(id)
    """
    qs = RealizedTrade.objects.filter(user=user, trade_at__gte=start)
    if end is not None:
        qs = qs.filter(trade_at__lte=end)
    if broker:
        qs = qs.filter(broker=broker)

    qs = _with_pnl_jpy(qs)

    agg = qs.aggregate(
        pnl=Coalesce(Sum("pnl_jpy_calc", output_field=DEC2), Value(Decimal("0"), output_field=DEC2)),
        cnt=Coalesce(Count("id"), Value(0)),
    )

    return Decimal(agg["pnl"]), int(agg["cnt"])


def _broker_rows_ytd(user, start: date) -> List[Dict[str, Any]]:
    rows = []
    for key in ("RAKUTEN", "SBI", "MATSUI", "OTHER"):
        total, cnt = _sum_pnl_all(user, start=start, broker=key)
        rows.append({
            "broker": key,
            "label": BROKER_LABEL.get(key, key),
            "ytd": float(total),
            "count": cnt,
        })
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

    total_abs = sum(abs(r["ytd"]) for r in by_broker_rows) or 1.0

    by_rows = []
    for r in by_broker_rows:
        weight = abs(r["ytd"]) / total_abs
        goal_b = float(goal_year) * weight
        remain_b = goal_b - float(r["ytd"])
        need_m_b = remain_b / rem_m if rem_m > 0 else remain_b

        by_rows.append({
            "broker": r["broker"],
            "label": r["label"],
            "ytd": r["ytd"],
            "goal_year": int(round(goal_b)),
            "pace_month": {
                "remaining": remain_b,
                "need_per_slot": need_m_b,
            }
        })

    return {
        "remaining_months_including_current": rem_m,
        "remaining_weeks_including_current": rem_w,
        "total_need_per_month": {
            "remaining": float(remain),
            "need_per_slot": float(need_m),
        },
        "total_need_per_week": {
            "remaining": float(remain),
            "need_per_slot": float(need_w),
        },
        "by_broker_rows": by_rows,
    }


# =========================
# Public API
# =========================
def build_assets_snapshot(user) -> Dict[str, Any]:
    d = _today()

    ytd_start = _year_start(d)
    mtd_start = _month_start(d)
    wtd_start = _week_start_monday(d)

    ytd_total, ytd_cnt = _sum_pnl_all(user, start=ytd_start)
    mtd_total, mtd_cnt = _sum_pnl_all(user, start=mtd_start)
    wtd_total, wtd_cnt = _sum_pnl_all(user, start=wtd_start)

    # 年間目標（将来拡張）
    setting, _ = UserSetting.objects.get_or_create(user=user)
    goal_year_total = Decimal("0")
    # 例：
    # goal_year_total = Decimal(setting.realized_goal_year or 0)

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