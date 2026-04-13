"""
[FILE] autotrade/jobs/decide_strategy.py
[PATH] <project_root>/autotrade/jobs/decide_strategy.py

このファイルは何？
- 9:30 に動く「戦略決定＆ルール確定ジョブ」です。

重要（BREAKOUT一本運用）：
- gate判定は朝（morning_prepare/runner）でExecution基準で確定済み。
- 9:30 job は gate を再計算しない（上書き事故を防ぐ）。
- 実運用の chosen は BREAKOUT 以外にならない（VWAP等は選ばない）。
- gate_reason は朝の判定理由を尊重し、9:30で壊さない（空なら最低限だけ補完）

今回の変更：
- 直近5営業日/10営業日の実Executionをここで診断する
- 朝の見た目gateとは別に、「実運用だけ」の effective gate を決める
- LONG_WEAK / OVERTRADING / TIME_BIASED を intraday 用ルールへ反映する
- STOPに落としすぎないよう、まずは LIGHT で耐える方向に調整する
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

from django.conf import settings
from django.utils import timezone

from autotrade.models import AutoTradeDailyState, AutoTradeSettingSnapshot
from autotrade.models_backtest import AutoTradeExecution
from autotrade.services.decision.strategy import decide_strategy_for_state
from autotrade.services.decision.rules import build_rules_for_today
from autotrade.services.common.guards import is_emergency_stopped


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


def _get_gate_final_from_state(state: AutoTradeDailyState):
    """
    morning_prepare が作った backtest['gate']['final'] を読む。
    無い場合は安全にSTOP扱い。
    """
    bt = state.backtest if isinstance(state.backtest, dict) else {}
    gate = bt.get("gate") if isinstance(bt.get("gate"), dict) else {}
    final = gate.get("final") if isinstance(gate.get("final"), dict) else {}

    gate_level = str(final.get("gate_level") or state.gate_level or "STOP").upper().strip()
    active = list(final.get("active") or [])
    disabled = list(final.get("disabled") or [])
    return gate_level, active, disabled


def _local_trade_dt(exe: AutoTradeExecution):
    dt = getattr(exe, "exit_at", None) or getattr(exe, "created_at", None)
    if dt is None:
        return None
    try:
        return timezone.localtime(dt)
    except Exception:
        return dt


def _local_trade_date(exe: AutoTradeExecution) -> Optional[date]:
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


def _recent_business_dates(*, target_date: date, count: int, include_today: bool) -> List[date]:
    qs = AutoTradeDailyState.objects.all()

    if include_today:
        qs = qs.filter(date__lte=target_date)
    else:
        qs = qs.filter(date__lt=target_date)

    dates: List[date] = []
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


def _summarize_recent_period(
    *,
    user,
    target_date: date,
    count: int,
    include_today: bool,
    mode: str = "PAPER",
    strategy: str = "BREAKOUT",
    base_equity_yen: int = 1_000_000,
) -> Dict[str, Any]:
    dates = _recent_business_dates(target_date=target_date, count=count, include_today=include_today)
    date_set = set(dates)

    bucket: Dict[date, List[AutoTradeExecution]] = defaultdict(list)

    qs = (
        AutoTradeExecution.objects
        .filter(user=user, strategy=str(strategy).upper())
        .exclude(mode="BACKTEST")
        .order_by("exit_at", "id")
    )

    if str(mode).upper().strip() in ["PAPER", "LIVE"]:
        qs = qs.filter(mode=str(mode).upper().strip())

    for e in qs:
        d = _local_trade_date(e)
        if d in date_set:
            bucket[d].append(e)

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


def _build_recent_runtime_diagnosis(*, user, target_date: date) -> Dict[str, Any]:
    base_equity_yen = int(getattr(settings, "AUTOTRADE_BASE_EQUITY_YEN", 1_000_000))

    w5 = _summarize_recent_period(
        user=user,
        target_date=target_date,
        count=5,
        include_today=False,
        mode="PAPER",
        strategy="BREAKOUT",
        base_equity_yen=base_equity_yen,
    )
    w10 = _summarize_recent_period(
        user=user,
        target_date=target_date,
        count=10,
        include_today=False,
        mode="PAPER",
        strategy="BREAKOUT",
        base_equity_yen=base_equity_yen,
    )

    tags: List[str] = []

    if (
        _safe_int(w5.get("trades"), 0) >= 3
        and _safe_float(w5.get("pf"), 0.0) < 0.80
        and _safe_int(w5.get("pnl_sum_yen"), 0) < 0
    ):
        tags.append("RECENT_BREAKDOWN")

    if (
        _safe_int(w10.get("trades"), 0) >= 8
        and _safe_float(w10.get("pf"), 0.0) < 0.90
        and _safe_int(w10.get("pnl_sum_yen"), 0) < 0
    ):
        tags.append("NO_EDGE")

    if _safe_float(w10.get("sl_ratio"), 0.0) >= 0.40 and _safe_int(w10.get("sl_count"), 0) >= 5:
        tags.append("SL_BIASED")

    if _safe_float(w10.get("time_ratio"), 0.0) >= 0.55 and _safe_int(w10.get("time_count"), 0) >= 6:
        tags.append("TIME_BIASED")

    if _safe_int(w10.get("current_minus_streak"), 0) >= 3:
        tags.append("LOSING_STREAKY")

    if (
        _safe_int(w10.get("long_trades"), 0) >= 6
        and _safe_int(w10.get("long_pnl_yen"), 0) < 0
        and _safe_int(w10.get("short_pnl_yen"), 0) >= 0
    ):
        tags.append("LONG_WEAK")

    if (
        _safe_int(w10.get("trades"), 0) >= 20
        and (
            _safe_float(w10.get("time_ratio"), 0.0) >= 0.60
            or (_safe_int(w10.get("trades"), 0) / max(1, _safe_int(w10.get("business_days"), 1))) >= 2.2
        )
    ):
        tags.append("OVERTRADING")

    tags = list(dict.fromkeys(tags))
    regime_warning = "RECENT_WEAK" if tags else "NORMAL"

    return {
        "as_of_date": str(target_date),
        "phase": "RUNTIME",
        "include_today": False,
        "mode": "PAPER",
        "strategy": "BREAKOUT",
        "regime_warning": regime_warning,
        "diagnosis_tags": tags,
        "windows": {
            "5": w5,
            "10": w10,
        },
        "summary_text": (
            f"5日PF={_safe_float(w5.get('pf'), 0.0):.3f} 損益={_safe_int(w5.get('pnl_sum_yen'), 0)}円 / "
            f"10日PF={_safe_float(w10.get('pf'), 0.0):.3f} 損益={_safe_int(w10.get('pnl_sum_yen'), 0)}円 / "
            f"tags={','.join(tags) if tags else 'NONE'}"
        ),
    }


def _build_runtime_control(*, morning_gate_level: str, diagnosis: Dict[str, Any]) -> Dict[str, Any]:
    """
    実運用だけの弱気補正。
    方針：
    - STOP はかなり重い条件の時だけ
    - まずは LIGHT に落として回数/保有/片側停止で耐える
    """
    morning_gate = str(morning_gate_level or "STOP").upper().strip()
    diagnosis = diagnosis if isinstance(diagnosis, dict) else {}

    windows = diagnosis.get("windows") if isinstance(diagnosis.get("windows"), dict) else {}
    w5 = (windows.get("5") or {}) if isinstance(windows, dict) else {}
    w10 = (windows.get("10") or {}) if isinstance(windows, dict) else {}

    tags = [str(x) for x in (diagnosis.get("diagnosis_tags") or []) if str(x).strip()]
    regime_warning = str(diagnosis.get("regime_warning") or "").strip()

    pf5 = _safe_float(w5.get("pf"), 0.0)
    pf10 = _safe_float(w10.get("pf"), 0.0)
    pnl5 = _safe_int(w5.get("pnl_sum_yen"), 0)
    pnl10 = _safe_int(w10.get("pnl_sum_yen"), 0)
    trades5 = _safe_int(w5.get("trades"), 0)
    trades10 = _safe_int(w10.get("trades"), 0)
    minus_days5 = _safe_int(w5.get("minus_days"), 0)
    minus_days10 = _safe_int(w10.get("minus_days"), 0)
    streak5 = _safe_int(w5.get("current_minus_streak"), 0)
    streak10 = _safe_int(w10.get("current_minus_streak"), 0)

    recent_breakdown = "RECENT_BREAKDOWN" in tags
    no_edge = "NO_EDGE" in tags
    sl_biased = "SL_BIASED" in tags
    time_biased = "TIME_BIASED" in tags
    losing_streaky = "LOSING_STREAKY" in tags
    long_weak = "LONG_WEAK" in tags
    overtrading = "OVERTRADING" in tags

    # まずは LIGHT に寄せたいので、STOP はかなり重くする
    catastrophic_stop = (
        trades5 >= 6
        and pf5 < 0.25
        and pnl5 < 0
        and minus_days5 >= 3
        and streak5 >= 4
        and trades10 >= 12
        and pf10 < 0.55
        and pnl10 < 0
        and minus_days10 >= 5
        and streak10 >= 4
        and no_edge
        and losing_streaky
    )

    # FULLのままだと危ないが、STOPまでは行かないケース
    light_needed = False
    if regime_warning == "RECENT_WEAK":
        light_needed = True
    if recent_breakdown and trades5 >= 4 and pf5 < 0.70 and pnl5 < 0:
        light_needed = True
    if no_edge and trades10 >= 10 and pf10 < 0.85 and pnl10 < 0:
        light_needed = True
    if losing_streaky and streak10 >= 3:
        light_needed = True

    effective_gate = morning_gate
    runtime_notes: List[str] = []

    if morning_gate == "FULL":
        if catastrophic_stop:
            effective_gate = "STOP"
            runtime_notes.append("直近がかなり悪いので、実運用はSTOPに落とします。")
        elif light_needed:
            effective_gate = "LIGHT"
            runtime_notes.append("直近悪化を考慮して、実運用はLIGHTに落とします。")
    elif morning_gate == "LIGHT":
        if catastrophic_stop:
            effective_gate = "STOP"
            runtime_notes.append("LIGHTでも直近がかなり悪いため、実運用はSTOPに落とします。")
        else:
            effective_gate = "LIGHT"

    allow_long = True
    allow_short = True

    # 片側だけ弱い時は、その側だけ止める
    if long_weak and effective_gate in ["FULL", "LIGHT"]:
        allow_long = False
        runtime_notes.append("LONG_WEAK のため、今日はロング新規を禁止します。")

    max_positions_override = None
    if effective_gate in ["FULL", "LIGHT"]:
        if sl_biased and recent_breakdown:
            max_positions_override = 1
            runtime_notes.append("SL偏重かつ直近悪化のため、同時ポジション数を1に絞ります。")

    max_trades_override = None
    if effective_gate == "FULL":
        if overtrading and time_biased:
            max_trades_override = 3
            runtime_notes.append("OVERTRADING + TIME_BIASED のため、当日回数を3回までに抑えます。")
        elif overtrading:
            max_trades_override = 4
            runtime_notes.append("OVERTRADING のため、当日回数を4回までに抑えます。")
        elif time_biased:
            max_trades_override = 4
            runtime_notes.append("TIME_BIASED のため、当日回数を4回までに抑えます。")
    elif effective_gate == "LIGHT":
        if overtrading and time_biased:
            max_trades_override = 1
            runtime_notes.append("OVERTRADING + TIME_BIASED のため、当日回数を1回までに絞ります。")
        elif overtrading or time_biased:
            max_trades_override = 2
            runtime_notes.append("直近悪化を考慮して、当日回数を2回までに絞ります。")

    max_hold_bars_cap = None
    if effective_gate in ["FULL", "LIGHT"] and time_biased:
        if effective_gate == "FULL":
            max_hold_bars_cap = 8
            runtime_notes.append("TIME_BIASED のため、最大保有を8本までに短縮します。")
        else:
            max_hold_bars_cap = 6
            runtime_notes.append("TIME_BIASED のため、最大保有を6本までに短縮します。")

    return {
        "original_gate_level": morning_gate,
        "effective_gate_level": effective_gate,
        "regime_warning": regime_warning,
        "diagnosis_tags": tags,
        "allow_long": bool(allow_long),
        "allow_short": bool(allow_short),
        "max_positions_override": max_positions_override,
        "max_trades_override": max_trades_override,
        "max_hold_bars_cap": max_hold_bars_cap,
        "runtime_notes": runtime_notes,
    }


def _build_gate_reason_suffix(runtime_control: Dict[str, Any]) -> str:
    rc = runtime_control if isinstance(runtime_control, dict) else {}
    lines: List[str] = []

    original_gate = str(rc.get("original_gate_level") or "").strip()
    effective_gate = str(rc.get("effective_gate_level") or "").strip()

    if original_gate and effective_gate and original_gate != effective_gate:
        lines.append(f"【実運用調整】直近悪化のため {original_gate} → {effective_gate}")

    if rc.get("allow_long") is False and rc.get("allow_short", True):
        lines.append("【実運用調整】LONG_WEAK のためロング新規を禁止")

    if rc.get("allow_short") is False and rc.get("allow_long", True):
        lines.append("【実運用調整】SHORT側が弱いためショート新規を禁止")

    if rc.get("max_positions_override") is not None:
        lines.append(f"【実運用調整】同時ポジション上限={_safe_int(rc.get('max_positions_override'), 1)}")

    if rc.get("max_trades_override") is not None:
        lines.append(f"【実運用調整】当日回数上限={_safe_int(rc.get('max_trades_override'), 0)}")

    if rc.get("max_hold_bars_cap") is not None:
        lines.append(f"【実運用調整】最大保有={_safe_int(rc.get('max_hold_bars_cap'), 0)}本")

    return "\n".join(lines)


def run():
    today = date.today()
    state, _ = AutoTradeDailyState.objects.get_or_create(date=today)

    # =========================================================
    # 0) 非常停止ガード（最優先）
    # =========================================================
    if is_emergency_stopped(state):
        return

    # =========================================================
    # 1) 朝統計を 9:30 時点で固定保存（再現性の要）
    # =========================================================
    try:
        from autotrade.services.universe.morning_data_service import get_morning_stats
        state.morning_stats = get_morning_stats(state=state) or {}
    except Exception:
        state.morning_stats = {
            "range_pct": 0.0,
            "trend_pct": 0.0,
            "chop_ratio": 0.0,
            "tickers_used": [],
            "n_used": 0,
            "note": "error",
        }

    # =========================================================
    # 2) 朝統計ベースの“提案”は読む（ただし実運用の選択はBREAKOUT固定）
    # =========================================================
    decision = decide_strategy_for_state(state)
    wanted_raw = str(decision.strategy or "")

    # =========================================================
    # 3) gate は「朝のExecution基準」を正として読む（再計算しない）
    # =========================================================
    morning_gate_level, active, disabled = _get_gate_final_from_state(state)

    # =========================================================
    # 4) recent weakness を診断し、実運用だけの effective gate を決める
    # =========================================================
    active_snapshot = (
        AutoTradeSettingSnapshot.objects
        .filter(status="ACTIVE")
        .order_by("-created_at")
        .first()
    )

    if active_snapshot is not None:
        diagnosis = _build_recent_runtime_diagnosis(user=active_snapshot.user, target_date=today)
    else:
        diagnosis = {
            "as_of_date": str(today),
            "phase": "RUNTIME",
            "include_today": False,
            "mode": "PAPER",
            "strategy": "BREAKOUT",
            "regime_warning": "NO_ACTIVE",
            "diagnosis_tags": [],
            "windows": {},
            "summary_text": "ACTIVE snapshot がないため runtime diagnosis を作れませんでした。",
        }

    runtime_control = _build_runtime_control(
        morning_gate_level=morning_gate_level,
        diagnosis=diagnosis,
    )

    effective_gate_level = str(runtime_control.get("effective_gate_level") or morning_gate_level).upper().strip()

    # =========================================================
    # 5) 実運用の chosen は BREAKOUT 以外にならない
    # =========================================================
    if effective_gate_level == "STOP":
        chosen = ""
    else:
        chosen = "BREAKOUT" if "BREAKOUT" in active else ""

    # =========================================================
    # 6) state を更新（gate_reasonは朝のものを壊さない）
    # =========================================================
    state.strategy = chosen
    state.strategy_decided_at = timezone.now()

    state.strategy_decision = {
        "strategy_wanted": wanted_raw,
        "strategy": chosen,
        "confidence": float(getattr(decision, "confidence", 0.0)),
        "reason": getattr(decision, "reason", ""),
        "debug": getattr(decision, "debug", {}),
        "morning_gate_level": morning_gate_level,
        "effective_gate_level": effective_gate_level,
        "gate_active": active,
        "gate_disabled": disabled,
        "diagnosis": diagnosis,
        "runtime_control": runtime_control,
        "created_at": timezone.localtime(timezone.now()).isoformat(),
        "mode": "BREAKOUT_ONLY",
    }

    prev_rules = state.rules if isinstance(state.rules, dict) else {}
    preserved_rule_keys = [
        "execution_mode",
        "paper_logs",
        "live_logs",
        "paper_open_positions",
        "paper_pending_orders",
        "paper_last_bar_ts",
        "live_open_positions",
        "live_pending_orders",
        "live_last_bar_ts",
        "intraday_guard",
    ]

    equity_yen = state.equity_yen or getattr(settings, "AUTOTRADE_BASE_EQUITY_YEN", 1_000_000)
    new_rules = build_rules_for_today(
        gate_level=str(effective_gate_level),
        strategy=str(chosen) if chosen else None,
        equity_yen=int(equity_yen),
        diagnosis=diagnosis,
        runtime_control=runtime_control,
        original_gate_level=morning_gate_level,
    )

    for key in preserved_rule_keys:
        if key in prev_rules:
            new_rules[key] = prev_rules[key]

    state.rules = new_rules
    state.gate_level = str(effective_gate_level)

    if not (state.gate_reason or "").strip():
        if morning_gate_level == "STOP":
            state.gate_reason = "【最終判定】STOP（安全のため稼働しない）"
        elif morning_gate_level == "LIGHT":
            state.gate_reason = "【最終判定】LIGHT（慎重に稼働）"
        else:
            state.gate_reason = "【最終判定】FULL（通常稼働）"

    runtime_suffix = _build_gate_reason_suffix(runtime_control)
    if runtime_suffix:
        base_reason = (state.gate_reason or "").rstrip()
        if runtime_suffix not in base_reason:
            state.gate_reason = (base_reason + "\n" + runtime_suffix).strip()

    state.updated_at = timezone.now()
    state.save()