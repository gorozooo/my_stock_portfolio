# portfolio/services/home_assets.py
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Tuple

from django.db.models import Sum, Count, F, ExpressionWrapper, DecimalField
from django.db.models.functions import Coalesce
from django.utils import timezone

from ..models import RealizedTrade, UserSetting


BROKER_LABEL = {
    "RAKUTEN": "楽天証券",
    "SBI": "SBI証券",
    "MATSUI": "松井証券",
    "OTHER": "その他",
}


def _d0(x) -> Decimal:
    try:
        if x is None:
            return Decimal("0")
        return Decimal(str(x))
    except Exception:
        return Decimal("0")


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
    # Monday start
    return d - timedelta(days=d.weekday())


def _remaining_months_including_current(d: date) -> int:
    # 例: 12月なら1、1月なら12
    return (12 - d.month) + 1


def _remaining_weeks_including_current(d: date) -> int:
    # 雑に「今年の残り週数」を52週ベースで扱う
    iso_week = int(d.isocalendar().week)
    return max(1, 52 - iso_week + 1)


def _pnl_expr_db():
    """
    RealizedTrade.pnl と同じ定義をDB式で再現する。
      pnl = (price - basis)*qty - fee - tax
    ただし basis が NULL の場合は、破綻しないよう price を採用する（=差分0扱い）。
    """
    basis_eff = Coalesce(F("basis"), F("price"))
    return ExpressionWrapper(
        (F("price") - basis_eff) * F("qty") - F("fee") - F("tax"),
        output_field=DecimalField(max_digits=18, decimal_places=2),
    )


def _sum_pnl_sell(user, start: date, end: date | None = None, broker: str | None = None) -> Tuple[Decimal, int]:
    """
    ✅ 正解ロジック（スクショ3〜5に合わせる）：
    - RealizedTrade の SELL のみ
    - pnl（fee/tax控除後）を合計
    - trade_at で期間
    """
    qs = RealizedTrade.objects.filter(user=user, side="SELL", trade_at__gte=start)
    if end is not None:
        qs = qs.filter(trade_at__lte=end)
    if broker:
        qs = qs.filter(broker=broker)

    expr = _pnl_expr_db()
    agg = qs.aggregate(pnl_sum=Sum(expr), cnt=Count("id"))
    total = _d0(agg.get("pnl_sum"))
    cnt = int(agg.get("cnt") or 0)
    return total, cnt


def _broker_rows_ytd(user, start: date) -> List[Dict[str, Any]]:
    rows = []
    for key in ("RAKUTEN", "SBI", "MATSUI", "OTHER"):
        total, cnt = _sum_pnl_sell(user, start=start, broker=key)
        rows.append({
            "broker": key,
            "label": BROKER_LABEL.get(key, key),
            "ytd": float(total),
            "count": cnt,
        })
    return rows


def _build_pace(goal_year: Decimal, ytd: Decimal, d: date, by_broker_rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    remain = goal_year - ytd
    rem_m = _remaining_months_including_current(d)
    rem_w = _remaining_weeks_including_current(d)

    need_m = remain / Decimal(str(rem_m)) if rem_m > 0 else remain
    need_w = remain / Decimal(str(rem_w)) if rem_w > 0 else remain

    # broker別：今年目標は “全体目標をytd比で按分” (暫定)
    total_abs = sum(abs(r["ytd"]) for r in by_broker_rows) or 1.0

    by_rows = []
    for r in by_broker_rows:
        weight = abs(r["ytd"]) / total_abs
        goal_b = float(goal_year) * weight
        remain_b = goal_b - float(r["ytd"])
        need_m_b = remain_b / rem_m if rem_m > 0 else remain_b
        need_w_b = remain_b / rem_w if rem_w > 0 else remain_b

        by_rows.append({
            "broker": r["broker"],
            "label": r["label"],
            "ytd": r["ytd"],
            "goal_year": int(round(goal_b)),
            "pace_month": {"remaining": remain_b, "need_per_slot": need_m_b},
            "pace_week": {"remaining": remain_b, "need_per_slot": need_w_b},
        })

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

    # 年間目標（まだUserSettingにフィールドが無いので暫定0）
    setting, _ = UserSetting.objects.get_or_create(user=user)
    goal_year_total = Decimal("0")
    # 将来フィールドを追加したらここを読む
    # goal_year_total = _d0(getattr(setting, "realized_goal_year", 0))

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
        "by_broker": by_broker,
    }