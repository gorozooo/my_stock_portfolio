# portfolio/services/home_assets.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Dict, Any, Optional, Tuple, List

from django.utils import timezone

from ..models import RealizedTrade, UserSetting, Holding


# =========================
# helpers
# =========================
def _as_date(d: Optional[date] = None) -> date:
    if d:
        return d
    # timezone aware な today を date に落とす
    try:
        return timezone.localdate()
    except Exception:
        return date.today()


def _start_of_year(d: date) -> date:
    return date(d.year, 1, 1)


def _start_of_month(d: date) -> date:
    return date(d.year, d.month, 1)


def _start_of_week_monday(d: date) -> date:
    # Monday=0 ... Sunday=6
    return d - timedelta(days=d.weekday())


def _end_of_year(d: date) -> date:
    return date(d.year, 12, 31)


def _clamp_int(v: int, min_v: int = 0) -> int:
    return v if v >= min_v else min_v


def _broker_choices() -> Tuple[Tuple[str, str], ...]:
    return getattr(Holding, "BROKER_CHOICES", (
        ("RAKUTEN", "楽天証券"),
        ("SBI", "SBI証券"),
        ("MATSUI", "松井証券"),
        ("OTHER", "その他"),
    ))


def _broker_label_map() -> Dict[str, str]:
    return {k: v for k, v in _broker_choices()}


def _safe_int(x: Any, default: int = 0) -> int:
    try:
        if x is None:
            return default
        # Decimal / float / str 混在対策
        return int(float(x))
    except Exception:
        return default


def _calc_remaining_months(as_of: date) -> int:
    # “今月を含めて”残り月数（例: 12/29なら 12月のみ→1）
    return _clamp_int(12 - as_of.month + 1, 1)


def _calc_remaining_weeks(as_of: date) -> int:
    """
    “今日を含めて年末まで”の残り週ブロック数（7日単位）
    - ISO週(52/53)の罠を避ける
    - 体感に合う「残り何週」になる
    """
    end = _end_of_year(as_of)
    days = (end - as_of).days
    # 今日を含めるので +1 してから 7日単位にする
    return _clamp_int(((days + 1) + 6) // 7, 1)


def _trade_pnl_jpy(t: RealizedTrade) -> float:
    # プロパティ優先（US株なら pnl_jpy へ）
    try:
        return float(t.pnl_jpy)
    except Exception:
        try:
            return float(t.pnl)
        except Exception:
            return 0.0


def _sum_pnl_jpy(trades: List[RealizedTrade]) -> float:
    total = 0.0
    for t in trades:
        total += _trade_pnl_jpy(t)
    return total


def _sum_by_broker(trades: List[RealizedTrade]) -> Dict[str, float]:
    out: Dict[str, float] = {}
    for t in trades:
        b = (t.broker or "OTHER").upper()
        out[b] = out.get(b, 0.0) + _trade_pnl_jpy(t)
    return out


def _goal_by_broker(setting: UserSetting) -> Dict[str, int]:
    d = setting.year_goal_by_broker or {}
    out: Dict[str, int] = {}
    for k, _lbl in _broker_choices():
        out[k] = _safe_int(d.get(k), 0)
    return out


def _pace_from_remaining(goal: int, achieved: float, remaining_slots: int) -> Dict[str, Any]:
    """
    goal: 年間目標
    achieved: 既に達成した実現損益（YTD）
    remaining_slots: 残り月数 or 残り週数（今を含む）
    """
    remaining = float(goal) - float(achieved)
    pace = remaining / float(remaining_slots) if remaining_slots > 0 else remaining
    return {
        "goal": int(goal),
        "achieved": float(achieved),
        "remaining": float(remaining),
        "need_per_slot": float(pace),
    }


# =========================
# main
# =========================
def build_assets_snapshot(user, as_of: Optional[date] = None) -> Dict[str, Any]:
    """
    Home / ASSETS 用（リアルタイム）
    - 実現損益（YTD/MTD/WTD）
    - 年間目標（全体＋broker別）と、残り月/週の必要ペース
    """
    d = _as_date(as_of)

    y0 = _start_of_year(d)
    m0 = _start_of_month(d)
    w0 = _start_of_week_monday(d)

    # 対象：そのユーザーの実現トレード
    # ※ pnl はDB列じゃないので、範囲を絞って Python で合算する（Home用に十分軽い）
    qs_ytd = list(
        RealizedTrade.objects.filter(user=user, trade_at__gte=y0, trade_at__lte=d).order_by("trade_at", "id")
    )
    qs_mtd = list(
        RealizedTrade.objects.filter(user=user, trade_at__gte=m0, trade_at__lte=d).order_by("trade_at", "id")
    )
    qs_wtd = list(
        RealizedTrade.objects.filter(user=user, trade_at__gte=w0, trade_at__lte=d).order_by("trade_at", "id")
    )

    ytd_total = _sum_pnl_jpy(qs_ytd)
    mtd_total = _sum_pnl_jpy(qs_mtd)
    wtd_total = _sum_pnl_jpy(qs_wtd)

    ytd_by_broker = _sum_by_broker(qs_ytd)
    mtd_by_broker = _sum_by_broker(qs_mtd)
    wtd_by_broker = _sum_by_broker(qs_wtd)

    # UserSetting（年目標）
    setting, _ = UserSetting.objects.get_or_create(user=user)
    goal_total = int(getattr(setting, "year_goal_total", 0) or 0)
    goal_broker = _goal_by_broker(setting)

    # 残りスロット数
    rem_months = _calc_remaining_months(d)
    rem_weeks = _calc_remaining_weeks(d)

    # 全体ペース
    pace_total_month = _pace_from_remaining(goal_total, ytd_total, rem_months)
    pace_total_week = _pace_from_remaining(goal_total, ytd_total, rem_weeks)

    # broker別ペース
    broker_labels = _broker_label_map()
    broker_rows = []
    for key, label in _broker_choices():
        g = int(goal_broker.get(key, 0) or 0)
        a = float(ytd_by_broker.get(key, 0.0))
        broker_rows.append({
            "broker": key,
            "label": label,
            "ytd": a,
            "goal_year": g,
            "pace_month": _pace_from_remaining(g, a, rem_months),
            "pace_week": _pace_from_remaining(g, a, rem_weeks),
        })

    # 表示に使える“整形済み”まとめ
    snapshot: Dict[str, Any] = {
        "as_of": d.isoformat(),
        "ranges": {
            "ytd_start": y0.isoformat(),
            "mtd_start": m0.isoformat(),
            "wtd_start": w0.isoformat(),
        },
        "realized": {
            "ytd": {"total": ytd_total, "by_broker": ytd_by_broker, "count": len(qs_ytd)},
            "mtd": {"total": mtd_total, "by_broker": mtd_by_broker, "count": len(qs_mtd)},
            "wtd": {"total": wtd_total, "by_broker": wtd_by_broker, "count": len(qs_wtd)},
        },
        "goals": {
            "year_total": goal_total,
            "year_by_broker": goal_broker,
        },
        "pace": {
            "remaining_months_including_current": rem_months,
            "remaining_weeks_including_current": rem_weeks,
            "total_need_per_month": pace_total_month,  # goal/achieved/remaining/need_per_slot
            "total_need_per_week": pace_total_week,
            "by_broker_rows": broker_rows,
        },
        "meta": {
            "broker_labels": broker_labels,
        },
    }
    return snapshot