"""
[FILE] recent_diagnosis.py
[PATH] <project_root>/autotrade/services/tuning/recent_diagnosis.py

このファイルは何？
- 直近の DEMO 実Execution を見て、「最近どう壊れているか」を診断する部品です。
- 朝(MORNING)は前営業日まで、引け後(EOD)は当日込みで診断します。
- 5営業日 / 10営業日の悪化傾向から、優先して触るべきノブ候補を返します。
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date as dt_date, timedelta
from typing import Any, Dict, List, Optional, Tuple

from django.utils import timezone

from autotrade.models import AutoTradeDailyState
from autotrade.models_backtest import AutoTradeExecution


def _safe_int(x: Any, default: int = 0) -> int:
    try:
        return int(x)
    except Exception:
        return int(default)


def _safe_float(x: Any, default: float = 0.0) -> float:
    try:
        return float(x)
    except Exception:
        return float(default)


def _local_trade_dt(exe: AutoTradeExecution):
    dt = getattr(exe, "exit_at", None) or getattr(exe, "created_at", None)
    if dt is None:
        return None
    try:
        return timezone.localtime(dt)
    except Exception:
        return dt


def _local_trade_date(exe: AutoTradeExecution) -> Optional[dt_date]:
    dt = _local_trade_dt(exe)
    if dt is None:
        return None
    try:
        return dt.date()
    except Exception:
        return None


def _calc_max_drawdown_yen(points: List[int]) -> int:
    peak = None
    max_dd = 0
    for x in points or []:
        v = _safe_int(x, 0)
        if peak is None or v > peak:
            peak = v
        if peak is not None:
            dd = peak - v
            if dd > max_dd:
                max_dd = dd
    return int(max_dd)


def _recent_business_dates(
    *,
    target_date: dt_date,
    count: int,
    include_today: bool,
) -> List[dt_date]:
    qs = AutoTradeDailyState.objects.all()

    if include_today:
        qs = qs.filter(date__lte=target_date)
    else:
        qs = qs.filter(date__lt=target_date)

    dates: List[dt_date] = []
    seen = set()

    for d in qs.order_by("-date").values_list("date", flat=True):
        if d in seen:
            continue
        if getattr(d, "weekday", lambda: 6)() >= 5:
            continue
        seen.add(d)
        dates.append(d)
        if len(dates) >= int(count):
            break

    cur = target_date if include_today else (target_date - timedelta(days=1))
    while len(dates) < int(count):
        if cur.weekday() < 5 and cur not in seen:
            dates.append(cur)
            seen.add(cur)
        cur -= timedelta(days=1)

    dates.sort()
    return dates


def _summarize_period(
    *,
    dates: List[dt_date],
    bucket: Dict[dt_date, List[AutoTradeExecution]],
    base_equity_yen: int,
) -> Dict[str, Any]:
    ordered_dates = sorted(dates)
    daily_rows: List[Dict[str, Any]] = []
    ordered_execs: List[AutoTradeExecution] = []

    equity = int(base_equity_yen or 1_000_000)
    equity_points = [equity]

    wins = 0
    losses = 0
    sum_win = 0
    sum_loss = 0
    pnl_sum = 0

    sl_count = 0
    tp_count = 0
    time_count = 0
    force_count = 0
    eod_count = 0

    time_pnl_yen = 0
    long_pnl_yen = 0
    short_pnl_yen = 0
    long_trades = 0
    short_trades = 0

    hold_total = 0

    for d in ordered_dates:
        day_execs = sorted(
            list(bucket.get(d) or []),
            key=lambda x: (_local_trade_dt(x) or timezone.now(), getattr(x, "id", 0)),
        )

        day_pnl = 0
        for e in day_execs:
            ordered_execs.append(e)

            pnl = _safe_int(getattr(e, "pnl_yen", 0), 0)
            day_pnl += pnl
            pnl_sum += pnl

            if pnl >= 0:
                wins += 1
                sum_win += pnl
            else:
                losses += 1
                sum_loss += pnl

            equity += pnl
            equity_points.append(int(equity))

            exit_reason = str(getattr(e, "exit_reason", "") or "").upper().strip()
            if exit_reason == "SL":
                sl_count += 1
            elif exit_reason == "TP":
                tp_count += 1
            elif exit_reason == "TIME":
                time_count += 1
                time_pnl_yen += pnl
            elif exit_reason == "FORCE":
                force_count += 1
            elif exit_reason == "EOD":
                eod_count += 1

            side = str(getattr(e, "side", "") or "").upper().strip()
            if side == "LONG":
                long_trades += 1
                long_pnl_yen += pnl
            elif side == "SHORT":
                short_trades += 1
                short_pnl_yen += pnl

            hold_total += _safe_int(getattr(e, "holding_minutes", 0), 0)

        daily_rows.append(
            {
                "date": d.isoformat(),
                "trades": len(day_execs),
                "pnl_sum_yen": int(day_pnl),
            }
        )

    trades = len(ordered_execs)
    sum_loss_abs = abs(sum_loss)

    if trades > 0:
        win_rate = wins / trades
        avg_hold_minutes = hold_total / trades
    else:
        win_rate = 0.0
        avg_hold_minutes = 0.0

    if sum_loss_abs > 0:
        pf = float(sum_win) / float(sum_loss_abs)
    else:
        pf = 999.0 if sum_win > 0 else 0.0

    dd_yen = _calc_max_drawdown_yen(equity_points)
    dd_pct = (float(dd_yen) / float(base_equity_yen)) if base_equity_yen > 0 else 0.0

    plus_days = sum(1 for x in daily_rows if _safe_int(x.get("pnl_sum_yen"), 0) > 0)
    minus_days = sum(1 for x in daily_rows if _safe_int(x.get("pnl_sum_yen"), 0) < 0)
    flat_days = sum(1 for x in daily_rows if _safe_int(x.get("pnl_sum_yen"), 0) == 0 and _safe_int(x.get("trades"), 0) > 0)
    no_trade_days = sum(1 for x in daily_rows if _safe_int(x.get("trades"), 0) == 0)

    current_minus_streak = 0
    for row in reversed(daily_rows):
        pnl = _safe_int(row.get("pnl_sum_yen"), 0)
        trades_day = _safe_int(row.get("trades"), 0)

        if trades_day == 0:
            continue
        if pnl < 0:
            current_minus_streak += 1
            continue
        break

    return {
        "business_days": len(ordered_dates),
        "trades": int(trades),
        "wins": int(wins),
        "losses": int(losses),
        "win_rate_pct": round(win_rate * 100.0, 1),
        "pnl_sum_yen": int(pnl_sum),
        "sum_win_yen": int(sum_win),
        "sum_loss_abs_yen": int(sum_loss_abs),
        "pf": round(float(pf), 3),
        "dd_yen": int(dd_yen),
        "dd_pct": round(dd_pct * 100.0, 2),
        "sl_count": int(sl_count),
        "tp_count": int(tp_count),
        "time_count": int(time_count),
        "force_count": int(force_count),
        "eod_count": int(eod_count),
        "sl_ratio": round((sl_count / trades) if trades > 0 else 0.0, 3),
        "tp_ratio": round((tp_count / trades) if trades > 0 else 0.0, 3),
        "time_ratio": round((time_count / trades) if trades > 0 else 0.0, 3),
        "time_pnl_yen": int(time_pnl_yen),
        "avg_hold_minutes": round(float(avg_hold_minutes), 1),
        "plus_days": int(plus_days),
        "minus_days": int(minus_days),
        "flat_days": int(flat_days),
        "no_trade_days": int(no_trade_days),
        "current_minus_streak": int(current_minus_streak),
        "long_trades": int(long_trades),
        "short_trades": int(short_trades),
        "long_pnl_yen": int(long_pnl_yen),
        "short_pnl_yen": int(short_pnl_yen),
        "daily_rows": daily_rows,
    }


def _build_tags_and_moves(
    *,
    s5: Dict[str, Any],
    s10: Dict[str, Any],
) -> Tuple[List[str], List[Dict[str, Any]], Dict[str, int], str]:
    tags: List[str] = []
    move_scores: Dict[Tuple[str, str], int] = defaultdict(int)

    def add_tag(tag: str) -> None:
        if tag not in tags:
            tags.append(tag)

    def add_move(knob: str, prefer: str, weight: int) -> None:
        key = (str(knob), str(prefer))
        move_scores[key] = max(move_scores.get(key, 0), int(weight))

    if s5["trades"] >= 4 and s5["pnl_sum_yen"] < 0 and s5["pf"] < 1.0:
        add_tag("RECENT_BREAKDOWN")

    if s10["trades"] >= 8 and s10["pnl_sum_yen"] < 0 and s10["pf"] < 0.95:
        add_tag("NO_EDGE")

    if s5["trades"] >= 4 and s5["sl_ratio"] >= 0.50:
        add_tag("SL_BIASED")

    if s5["trades"] >= 4 and s5["time_ratio"] >= 0.40:
        add_tag("TIME_BIASED")

    if s5["trades"] >= 8 and s5["pf"] < 0.95:
        add_tag("OVERTRADING")

    if s5["current_minus_streak"] >= 2 or s10["minus_days"] >= max(3, s10["plus_days"] + 1):
        add_tag("LOSING_STREAKY")

    if s10["trades"] < 5:
        add_tag("TOO_FEW_TRADES")

    if s10["long_trades"] >= 4 and s10["long_pnl_yen"] < 0 and s10["short_pnl_yen"] > 0:
        add_tag("LONG_WEAK")

    if s10["short_trades"] >= 4 and s10["short_pnl_yen"] < 0 and s10["long_pnl_yen"] > 0:
        add_tag("SHORT_WEAK")

    if "RECENT_BREAKDOWN" in tags or "NO_EDGE" in tags:
        add_move("lookback_bars", "UP", 5)
        add_move("daily_filter", "TO_SMA", 4)
        add_move("direction", "TO_TREND_ONLY", 4)

        if s5["sl_ratio"] >= s5["time_ratio"]:
            add_move("stop_pct", "UP", 3)
            add_move("sma_days", "UP", 3)
        else:
            add_move("rr_breakout", "DOWN", 4)
            if s5["time_pnl_yen"] >= 0:
                add_move("max_hold_bars", "UP", 3)
            else:
                add_move("max_hold_bars", "DOWN", 2)

    if "SL_BIASED" in tags:
        add_move("lookback_bars", "UP", 5)
        add_move("daily_filter", "TO_SMA", 4)
        add_move("direction", "TO_TREND_ONLY", 4)
        add_move("stop_pct", "UP", 3)
        add_move("sma_days", "UP", 2)

    if "TIME_BIASED" in tags:
        add_move("rr_breakout", "DOWN", 5)
        if s5["time_pnl_yen"] >= 0:
            add_move("max_hold_bars", "UP", 3)
        else:
            add_move("max_hold_bars", "DOWN", 3)
        add_move("lookback_bars", "UP", 2)

    if "OVERTRADING" in tags:
        add_move("lookback_bars", "UP", 5)
        add_move("daily_filter", "TO_SMA", 4)
        add_move("sma_days", "UP", 4)
        add_move("direction", "TO_TREND_ONLY", 3)

    if "LOSING_STREAKY" in tags:
        add_move("lookback_bars", "UP", 3)
        add_move("daily_filter", "TO_SMA", 3)
        add_move("direction", "TO_TREND_ONLY", 3)

    if "TOO_FEW_TRADES" in tags:
        add_move("lookback_bars", "DOWN", 4)
        add_move("sma_days", "DOWN", 3)
        add_move("daily_filter", "TO_OFF", 4)
        add_move("direction", "TO_BOTH", 3)

    if "LONG_WEAK" in tags or "SHORT_WEAK" in tags:
        add_move("direction", "TO_TREND_ONLY", 3)
        add_move("daily_filter", "TO_SMA", 3)
        add_move("sma_days", "UP", 2)

    if not tags:
        tags.append("STABLE")

    preferred_moves = [
        {"knob": knob, "prefer": prefer, "weight": int(weight)}
        for (knob, prefer), weight in move_scores.items()
    ]
    preferred_moves.sort(key=lambda x: (-_safe_int(x.get("weight"), 0), str(x.get("knob") or ""), str(x.get("prefer") or "")))

    knob_scores: Dict[str, int] = defaultdict(int)
    for row in preferred_moves:
        knob_scores[str(row["knob"])] += _safe_int(row.get("weight"), 0)

    preferred_knobs = [k for k, _ in sorted(knob_scores.items(), key=lambda kv: (-kv[1], kv[0]))]

    if "RECENT_BREAKDOWN" in tags or "NO_EDGE" in tags:
        regime_warning = "RECENT_WEAK"
    elif "SL_BIASED" in tags or "TIME_BIASED" in tags or "LOSING_STREAKY" in tags:
        regime_warning = "CAUTION"
    else:
        regime_warning = "NORMAL"

    return tags, preferred_moves, dict(knob_scores), regime_warning


def build_recent_diagnosis(
    *,
    target_date: Optional[dt_date] = None,
    phase: str = "MORNING",
    mode: str = "PAPER",
    strategy: str = "BREAKOUT",
    base_equity_yen: int = 1_000_000,
) -> Dict[str, Any]:
    if target_date is None:
        target_date = timezone.localdate()

    phase = str(phase or "MORNING").upper().strip()
    include_today = phase == "EOD"

    windows = [5, 10]
    dates_by_window = {
        int(n): _recent_business_dates(
            target_date=target_date,
            count=int(n),
            include_today=include_today,
        )
        for n in windows
    }

    all_dates = set()
    for xs in dates_by_window.values():
        all_dates.update(xs)

    qs = (
        AutoTradeExecution.objects
        .filter(mode=str(mode or "PAPER").upper().strip(), strategy=str(strategy or "BREAKOUT").upper().strip())
        .exclude(mode="BACKTEST")
        .order_by("-exit_at", "-id")
    )[:2000]

    bucket: Dict[dt_date, List[AutoTradeExecution]] = defaultdict(list)
    for e in qs:
        d = _local_trade_date(e)
        if d is None:
            continue
        if d not in all_dates:
            continue
        bucket[d].append(e)

    s5 = _summarize_period(
        dates=dates_by_window[5],
        bucket=bucket,
        base_equity_yen=int(base_equity_yen),
    )
    s10 = _summarize_period(
        dates=dates_by_window[10],
        bucket=bucket,
        base_equity_yen=int(base_equity_yen),
    )

    tags, preferred_moves, knob_scores, regime_warning = _build_tags_and_moves(s5=s5, s10=s10)

    return {
        "as_of_date": str(target_date),
        "phase": phase,
        "include_today": bool(include_today),
        "mode": str(mode or "PAPER").upper().strip(),
        "strategy": str(strategy or "BREAKOUT").upper().strip(),
        "regime_warning": regime_warning,
        "diagnosis_tags": tags,
        "preferred_knobs": list(preferred_moves and [x["knob"] for x in preferred_moves] or []),
        "preferred_moves": preferred_moves,
        "knob_scores": knob_scores,
        "windows": {
            "5": s5,
            "10": s10,
        },
        "summary_text": (
            f"5日PF={s5['pf']:.3f} 損益={s5['pnl_sum_yen']}円 / "
            f"10日PF={s10['pf']:.3f} 損益={s10['pnl_sum_yen']}円 / "
            f"tags={','.join(tags)}"
        ),
    }