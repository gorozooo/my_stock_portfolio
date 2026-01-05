# portfolio/services/home_assets.py
from __future__ import annotations

import calendar
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


def _year_end(d: date) -> date:
    return date(d.year, 12, 31)


def _month_start(d: date) -> date:
    return date(d.year, d.month, 1)


def _month_end(d: date) -> date:
    last = calendar.monthrange(d.year, d.month)[1]
    return date(d.year, d.month, last)


def _week_start_monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _remaining_months_including_current(d: date) -> int:
    return (12 - d.month) + 1


def _remaining_weeks_including_current(d: date) -> int:
    iso_week = int(d.isocalendar().week)
    return max(1, 52 - iso_week + 1)


def _safe_yoy_pct(current: Decimal, prev: Decimal) -> float | None:
    """
    (current - prev) / prev * 100
    prev == 0 の場合は None（テンプレで "--" 表示）
    """
    try:
        if prev == 0:
            return None
        return float(((current - prev) / prev) * Decimal("100"))
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


def _broker_totals_for_period(
    user,
    start: date,
    end: date,
) -> Dict[str, Dict[str, Any]]:
    """
    broker -> {"total": Decimal, "count": int}
    """
    out: Dict[str, Dict[str, Any]] = {}
    for b in BROKERS:
        total, cnt = _sum_pnl_all(user, start=start, end=end, broker=b)
        out[b] = {"total": total, "count": cnt}
    return out


def _broker_rows_ytd(user, start: date) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for key in BROKERS:
        total, cnt = _sum_pnl_all(user, start=start, broker=key)
        rows.append({
            "broker": key,
            "label": BROKER_LABEL.get(key, key),
            "ytd": float(total),
            "count": cnt,
        })
    return rows


def _build_pace(
    goal_year_total: Decimal,
    goal_by_broker: Dict[str, Any],
    ytd_total: Decimal,
    d: date,
    by_broker_rows: List[Dict[str, Any]],
    prev_year_by_broker: Dict[str, Dict[str, Any]],
    prev_month_by_broker: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    remain = goal_year_total - ytd_total
    rem_m = _remaining_months_including_current(d)
    rem_w = _remaining_weeks_including_current(d)

    need_m = remain / Decimal(rem_m) if rem_m > 0 else remain
    need_w = remain / Decimal(rem_w) if rem_w > 0 else remain

    # YTD 比率（フォールバック用）
    total_abs = sum(abs(r["ytd"]) for r in by_broker_rows) or 1.0

    by_rows: List[Dict[str, Any]] = []
    for r in by_broker_rows:
        broker = r["broker"]

        # ★ broker別目標：設定があれば最優先
        if broker in goal_by_broker and goal_by_broker.get(broker, 0):
            goal_b = Decimal(str(goal_by_broker[broker]))
        else:
            weight = abs(r["ytd"]) / total_abs
            goal_b = goal_year_total * Decimal(str(weight))

        remain_b = goal_b - Decimal(str(r["ytd"]))
        need_m_b = remain_b / Decimal(rem_m) if rem_m > 0 else remain_b

        # --- 前年（年合計）/ 前年同月（月合計） ---
        prev_y = prev_year_by_broker.get(broker, {}).get("total", Decimal("0"))
        prev_m = prev_month_by_broker.get(broker, {}).get("total", Decimal("0"))

        curr_ytd = Decimal(str(r["ytd"]))
        # 今月MTDは broker別でも計算したいが、ここでは assets側で broker別MTDを作ってないので、
        # “前年同月比” は「今月MTD（broker別）」が必要。
        # → 今回は broker別MTDもここで計算して入れる（軽量・3社だけなのでOK）

        # broker別 今月MTD
        mtd_start = _month_start(d)
        mtd_end = d  # “今月MTD”は今日まで（これはMTDの通常定義）
        curr_mtd_total, _ = _sum_pnl_all(user, start=mtd_start, end=mtd_end, broker=broker)

        ytd_yoy = _safe_yoy_pct(curr_ytd, prev_y)
        mtd_yoy = _safe_yoy_pct(curr_mtd_total, prev_m)

        by_rows.append({
            "broker": broker,
            "label": r["label"],
            "ytd": r["ytd"],
            "goal_year": int(goal_b),
            "pace_month": {
                "remaining": float(remain_b),
                "need_per_slot": float(need_m_b),
            },

            # 追加
            "ytd_prev": float(prev_y),
            "ytd_yoy_pct": ytd_yoy,
            "mtd_prev": float(prev_m),
            "mtd_yoy_pct": mtd_yoy,
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
    mtd_total, mtd_cnt = _sum_pnl_all(user, start=mtd_start, end=d)  # 今月MTD（今日まで）
    wtd_total, wtd_cnt = _sum_pnl_all(user, start=wtd_start, end=d)  # 今週WTD（今日まで）

    # --- 前年（年合計：1/1〜12/31） ---
    prev_year = d.year - 1
    prev_year_start = date(prev_year, 1, 1)
    prev_year_end = date(prev_year, 12, 31)
    prev_year_total, prev_year_cnt = _sum_pnl_all(user, start=prev_year_start, end=prev_year_end)

    # --- 前年同月（“月合計”：1日〜月末） ---
    prev_month_start = date(prev_year, d.month, 1)
    prev_month_end = _month_end(prev_month_start)
    prev_month_total, prev_month_cnt = _sum_pnl_all(user, start=prev_month_start, end=prev_month_end)

    # --- ユーザー設定 ---
    setting, _ = UserSetting.objects.get_or_create(user=user)
    goal_year_total = Decimal(str(setting.year_goal_total or 0))
    goal_by_broker = setting.year_goal_by_broker or {}

    by_broker = _broker_rows_ytd(user, start=ytd_start)

    # --- broker別の前年（年合計）/ 前年同月（月合計） ---
    prev_year_by_broker = _broker_totals_for_period(user, prev_year_start, prev_year_end)
    prev_month_by_broker = _broker_totals_for_period(user, prev_month_start, prev_month_end)

    pace = _build_pace(
        goal_year_total=goal_year_total,
        goal_by_broker=goal_by_broker,
        ytd_total=ytd_total,
        d=d,
        by_broker_rows=by_broker,
        prev_year_by_broker=prev_year_by_broker,
        prev_month_by_broker=prev_month_by_broker,
    )

    # 全体の前年比/前年同月比（分母0はNone）
    ytd_yoy_pct = _safe_yoy_pct(ytd_total, prev_year_total)
    mtd_yoy_pct = _safe_yoy_pct(mtd_total, prev_month_total)

    return {
        "status": "ok",
        "as_of": timezone.now().isoformat(),
        "realized": {
            "ytd": {"total": float(ytd_total), "count": ytd_cnt},
            "mtd": {"total": float(mtd_total), "count": mtd_cnt},
            "wtd": {"total": float(wtd_total), "count": wtd_cnt},

            # 追加
            "year_prev": {"total": float(prev_year_total), "count": prev_year_cnt},
            "mtd_prev": {"total": float(prev_month_total), "count": prev_month_cnt},
            "ytd_yoy_pct": ytd_yoy_pct,
            "mtd_yoy_pct": mtd_yoy_pct,
        },
        "goals": {
            "year_total": int(goal_year_total),
        },
        "pace": pace,
    }