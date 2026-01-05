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
# 表示ラベル（3社のみ）
# =========================
BROKER_LABEL = {
    "RAKUTEN": "楽天証券",
    "SBI": "SBI証券",
    "MATSUI": "松井証券",
}

BROKERS = ("RAKUTEN", "SBI", "MATSUI")

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


def _month_end(d: date) -> date:
    # 翌月1日 - 1日
    if d.month == 12:
        nxt = date(d.year + 1, 1, 1)
    else:
        nxt = date(d.year, d.month + 1, 1)
    return nxt - timedelta(days=1)


def _week_start_monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _remaining_months_including_current(d: date) -> int:
    return (12 - d.month) + 1


def _remaining_weeks_including_current(d: date) -> int:
    iso_week = int(d.isocalendar().week)
    return max(1, 52 - iso_week + 1)


def _pct_change(curr: Decimal, prev: Decimal) -> float | None:
    """
    (curr - prev) / abs(prev) * 100
    prev が 0 のときは None（テンプレ側で "—"）
    """
    try:
        if prev is None:
            return None
        if prev == 0:
            return None
        return float((curr - prev) / abs(prev) * Decimal("100"))
    except Exception:
        return None


# =========================
# realized.py と同一の円換算PnL
# =========================
def _with_pnl_jpy(qs):
    """
    realized.py の pnl_jpy_calc と同義：
      pnl_jpy_calc = cashflow * fx_to_jpy
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
    """
    qs = RealizedTrade.objects.filter(user=user, trade_at__gte=start)
    if end is not None:
        qs = qs.filter(trade_at__lte=end)
    if broker:
        qs = qs.filter(broker=broker)

    qs = _with_pnl_jpy(qs)

    agg = qs.aggregate(
        pnl=Coalesce(
            Sum("pnl_jpy_calc", output_field=DEC2),
            Value(Decimal("0"), output_field=DEC2),
        ),
        cnt=Coalesce(Count("id"), Value(0)),
    )

    return Decimal(agg["pnl"]), int(agg["cnt"])


def _broker_rows(
    user,
    ytd_start: date,
    mtd_start: date,
    mtd_end: date,
    prev_year_start: date,
    prev_year_end: date,
    prev_same_month_start: date,
    prev_same_month_end: date,
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for key in BROKERS:
        # 今年
        ytd_total, _ = _sum_pnl_all(user, start=ytd_start, broker=key)
        mtd_total, _ = _sum_pnl_all(user, start=mtd_start, end=mtd_end, broker=key)

        # 前年
        prev_year_total, _ = _sum_pnl_all(user, start=prev_year_start, end=prev_year_end, broker=key)
        prev_same_month_total, _ = _sum_pnl_all(user, start=prev_same_month_start, end=prev_same_month_end, broker=key)

        rows.append({
            "broker": key,
            "label": BROKER_LABEL.get(key, key),
            "ytd": float(ytd_total),
            "mtd": float(mtd_total),
            "prev_year_total": float(prev_year_total),
            "prev_same_month_total": float(prev_same_month_total),
            "ytd_yoy_pct": _pct_change(ytd_total, prev_year_total),
            "mtd_yoy_pct": _pct_change(mtd_total, prev_same_month_total),
        })
    return rows


def _build_pace(
    goal_year_total: Decimal,
    goal_by_broker: Dict[str, Any],
    ytd_total: Decimal,
    d: date,
    by_broker_rows: List[Dict[str, Any]],
) -> Dict[str, Any]:
    remain = goal_year_total - ytd_total
    rem_m = _remaining_months_including_current(d)
    rem_w = _remaining_weeks_including_current(d)

    need_m = remain / Decimal(rem_m) if rem_m > 0 else remain
    need_w = remain / Decimal(rem_w) if rem_w > 0 else remain

    # YTD 比率（フォールバック用）
    total_abs = sum(abs(r.get("ytd", 0.0)) for r in by_broker_rows) or 1.0

    by_rows = []
    for r in by_broker_rows:
        broker = r["broker"]

        # broker別目標：設定があれば最優先
        if broker in goal_by_broker and goal_by_broker.get(broker, 0):
            goal_b = Decimal(str(goal_by_broker[broker]))
        else:
            weight = abs(float(r.get("ytd", 0.0))) / float(total_abs)
            goal_b = goal_year_total * Decimal(str(weight))

        remain_b = goal_b - Decimal(str(r.get("ytd", 0.0)))
        need_m_b = remain_b / Decimal(rem_m) if rem_m > 0 else remain_b

        by_rows.append({
            "broker": broker,
            "label": r["label"],
            "ytd": r.get("ytd", 0.0),
            "goal_year": int(goal_b),
            "pace_month": {
                "remaining": float(remain_b),
                "need_per_slot": float(need_m_b),
            },
            # 追加（前年系）
            "prev_year_total": r.get("prev_year_total"),
            "ytd_yoy_pct": r.get("ytd_yoy_pct"),
            "mtd_yoy_pct": r.get("mtd_yoy_pct"),
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
    mtd_end = _month_end(d)
    wtd_start = _week_start_monday(d)

    # 前年（年合計）
    prev_year_start = date(d.year - 1, 1, 1)
    prev_year_end = date(d.year - 1, 12, 31)

    # 前年同月（その月の合計）
    prev_same_month_ref = date(d.year - 1, d.month, 1)
    prev_same_month_start = _month_start(prev_same_month_ref)
    prev_same_month_end = _month_end(prev_same_month_ref)

    # 当年
    ytd_total, ytd_cnt = _sum_pnl_all(user, start=ytd_start)
    mtd_total, mtd_cnt = _sum_pnl_all(user, start=mtd_start, end=mtd_end)
    wtd_total, wtd_cnt = _sum_pnl_all(user, start=wtd_start)

    # 前年
    prev_year_total, _ = _sum_pnl_all(user, start=prev_year_start, end=prev_year_end)
    prev_same_month_total, _ = _sum_pnl_all(user, start=prev_same_month_start, end=prev_same_month_end)

    # --- ユーザー設定 ---
    setting, _ = UserSetting.objects.get_or_create(user=user)
    goal_year_total = Decimal(str(setting.year_goal_total or 0))
    goal_by_broker = setting.year_goal_by_broker or {}

    by_broker = _broker_rows(
        user=user,
        ytd_start=ytd_start,
        mtd_start=mtd_start,
        mtd_end=mtd_end,
        prev_year_start=prev_year_start,
        prev_year_end=prev_year_end,
        prev_same_month_start=prev_same_month_start,
        prev_same_month_end=prev_same_month_end,
    )

    pace = _build_pace(
        goal_year_total=goal_year_total,
        goal_by_broker=goal_by_broker,
        ytd_total=ytd_total,
        d=d,
        by_broker_rows=by_broker,
    )

    return {
        "status": "ok",
        "as_of": timezone.now().isoformat(),
        "realized": {
            "ytd": {"total": float(ytd_total), "count": ytd_cnt},
            "mtd": {"total": float(mtd_total), "count": mtd_cnt},
            "wtd": {"total": float(wtd_total), "count": wtd_cnt},
            # 追加（全体）
            "prev_year_total": float(prev_year_total),
            "prev_same_month_total": float(prev_same_month_total),
            "ytd_yoy_pct": _pct_change(ytd_total, prev_year_total),
            "mtd_yoy_pct": _pct_change(mtd_total, prev_same_month_total),
        },
        "goals": {
            "year_total": int(goal_year_total),
        },
        "pace": pace,
    }