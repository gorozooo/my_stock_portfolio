# =========================================================
# [FILE] views_dashboard.py
# [PATH] <project_root>/autotrade/views_dashboard.py
#
# このファイルは何？
# - AutoTrade のダッシュボード表示 / 結果ページ / 日別履歴ページの View と整形関数です。
# - 今回はダッシュボードを「今日の運用 / 通算 / 診断 / 操作」の横スワイプ司令塔に作り直しています。
# - DEMO / LIVE の通算成績を日跨ぎでも消えない形で集計します。
# =========================================================

from __future__ import annotations

from collections import defaultdict
from datetime import date as dt_date, datetime, time as dt_time
from typing import Any, Dict, List, Optional, Tuple

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest
from django.shortcuts import render
from django.utils import timezone

from .models import AutoTradeDailyState, AutoTradeSettingSnapshot
from .views_utils import get_nested_dict

from autotrade.models_backtest import AutoTradeExecution
from autotrade.services.backtest.gate import GATE_THRESHOLDS


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


def _safe_bool(x: Any, default: bool = False) -> bool:
    try:
        if isinstance(x, bool):
            return x
        if x is None:
            return default
        s = str(x).strip().lower()
        if s in {"1", "true", "yes", "on"}:
            return True
        if s in {"0", "false", "no", "off"}:
            return False
        return bool(x)
    except Exception:
        return bool(default)


def _pct_100(x01: Any) -> float:
    v = _safe_float(x01, 0.0)
    return float(v * 100.0)


def _mode_label(mode: str) -> str:
    return "LIVE" if str(mode or "").upper().strip() == "LIVE" else "DEMO"


def _gate_class(level: str) -> str:
    lv = str(level or "STOP").upper().strip()
    if lv == "FULL":
        return "is-ok"
    if lv == "LIGHT":
        return "is-warn"
    return "is-bad"


def _get_nested(d: Dict[str, Any], keys: List[str], default: Any = None) -> Any:
    cur: Any = d
    for k in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(k)
    return cur if cur is not None else default


def _level_by_thresholds(metrics: Dict[str, Any]) -> str:
    dd = _safe_float(metrics.get("max_drawdown_pct"), 1.0)
    pf = _safe_float(metrics.get("profit_factor"), 0.0)
    trades = _safe_int(metrics.get("trades"), 0)

    win_rate = metrics.get("win_rate")
    if win_rate is None:
        wins = _safe_int(metrics.get("wins"), 0)
        win_rate = (wins / trades) if trades > 0 else 0.0
    win_rate = _safe_float(win_rate, 0.0)

    full = GATE_THRESHOLDS.get("FULL", {})
    light = GATE_THRESHOLDS.get("LIGHT", {})

    def _ok(th: Dict[str, Any]) -> bool:
        if dd > _safe_float(th.get("max_dd_pct"), 0.0):
            return False
        if pf < _safe_float(th.get("min_pf"), 0.0):
            return False
        if trades < _safe_int(th.get("min_trades"), 0):
            return False
        if "min_win_rate" in th and th.get("min_win_rate") is not None:
            if win_rate < _safe_float(th.get("min_win_rate"), 0.0):
                return False
        return True

    if _ok(full):
        return "FULL"
    if _ok(light):
        return "LIGHT"
    return "STOP"


def _miss_points(metrics: Dict[str, Any], *, base: str = "LIGHT") -> List[str]:
    th = (GATE_THRESHOLDS.get(base) or {}).copy()

    dd = _safe_float(metrics.get("max_drawdown_pct"), 1.0)
    pf = _safe_float(metrics.get("profit_factor"), 0.0)
    trades = _safe_int(metrics.get("trades"), 0)

    win_rate = metrics.get("win_rate")
    if win_rate is None:
        wins = _safe_int(metrics.get("wins"), 0)
        win_rate = (wins / trades) if trades > 0 else 0.0
    win_rate = _safe_float(win_rate, 0.0)

    out: List[str] = []

    max_dd = _safe_float(th.get("max_dd_pct"), 0.0)
    min_pf = _safe_float(th.get("min_pf"), 0.0)
    min_trades = _safe_int(th.get("min_trades"), 0)

    if pf < min_pf:
        out.append(f"PFが弱い（基準：{min_pf:.2f}以上）")
    if dd > max_dd:
        out.append(f"最大落ち込み（DD）が大きい（基準：{max_dd*100:.1f}%以内）")
    if trades < min_trades:
        out.append(f"取引回数が少ない（基準：{min_trades}回以上）")

    if "min_win_rate" in th and th.get("min_win_rate") is not None:
        min_wr = _safe_float(th.get("min_win_rate"), 0.0)
        if win_rate < min_wr:
            out.append(f"勝率が低い（基準：{min_wr*100:.1f}%以上）")

    return out[:3]


def _one_liner(level: str) -> str:
    if level == "FULL":
        return "安定しているので、通常稼働してOKです。"
    if level == "LIGHT":
        return "少し不安があるので、軽稼働（守り）ならOKです。"
    return "いまは不安定なので、停止が安全です。"


def build_result_cards_for_template(state: AutoTradeDailyState) -> List[Dict[str, Any]]:
    bt = state.backtest if isinstance(state.backtest, dict) else {}
    by_window = bt.get("by_window") if isinstance(bt.get("by_window"), dict) else {}

    meta = bt.get("meta") if isinstance(bt.get("meta"), dict) else {}
    windows = meta.get("windows")
    if not isinstance(windows, list) or not windows:
        windows = [20, 40, 60]

    out: List[Dict[str, Any]] = []

    for w in windows:
        w = _safe_int(w, 0)
        if w <= 0:
            continue

        m = get_nested_dict(by_window, int(w), "BREAKOUT", default={})
        if not isinstance(m, dict):
            m = {}

        trades = _safe_int(m.get("trades"), 0)
        wins = _safe_int(m.get("wins"), 0)
        losses = _safe_int(m.get("losses"), 0)

        win_rate = m.get("win_rate")
        if win_rate is None:
            win_rate = (wins / trades) if trades > 0 else 0.0
        win_rate = _safe_float(win_rate, 0.0)

        pf = _safe_float(m.get("profit_factor"), 0.0)
        dd_pct = _safe_float(m.get("max_drawdown_pct"), 0.0)
        dd_yen = _safe_int(m.get("max_drawdown_yen"), 0)
        pnl_yen = _safe_int(m.get("total_pnl"), 0)

        sum_win_yen = _safe_int(m.get("sum_win_yen"), 0)
        sum_loss_yen = _safe_int(m.get("sum_loss_yen"), 0)
        sum_loss_abs_yen = abs(sum_loss_yen)

        level = _level_by_thresholds(m)

        out.append({
            "strategy": "BREAKOUT",
            "window": int(w),
            "level": str(level),
            "one_liner": _one_liner(str(level)),
            "miss_points": _miss_points(m, base="LIGHT"),
            "trades": int(trades),
            "wins": int(wins),
            "losses": int(losses),
            "win_rate_pct": round(_pct_100(win_rate), 1),
            "pf": f"{pf:.3f}" if pf else f"{pf:.2f}",
            "dd_pct": round(_pct_100(dd_pct), 1),
            "dd_yen": int(dd_yen),
            "pnl_yen": int(pnl_yen),
            "sum_win_yen": int(sum_win_yen),
            "sum_loss_abs_yen": int(sum_loss_abs_yen),
        })

    return out


def build_thresholds_for_template() -> Dict[str, Any]:
    def _pack(name: str) -> Dict[str, Any]:
        th = (GATE_THRESHOLDS.get(name) or {}).copy()
        out: Dict[str, Any] = {
            "max_dd_pct": _safe_float(th.get("max_dd_pct"), 0.0) * 100.0,
            "min_pf": _safe_float(th.get("min_pf"), 0.0),
            "min_trades": _safe_int(th.get("min_trades"), 0),
            "min_win_rate": None,
            "min_win_rate_pct": None,
        }
        if "min_win_rate" in th and th.get("min_win_rate") is not None:
            out["min_win_rate"] = _safe_float(th.get("min_win_rate"), 0.0)
            out["min_win_rate_pct"] = round(_safe_float(th.get("min_win_rate"), 0.0) * 100.0, 1)
        return out

    return {
        "FULL": _pack("FULL"),
        "LIGHT": _pack("LIGHT"),
    }


def _flatten_dict(d: Any, *, prefix: str = "", max_depth: int = 3, depth: int = 0) -> List[Tuple[str, Any]]:
    out: List[Tuple[str, Any]] = []
    if not isinstance(d, dict):
        return out

    for k, v in d.items():
        key = f"{prefix}.{k}" if prefix else str(k)
        if isinstance(v, dict) and depth < max_depth:
            out.extend(_flatten_dict(v, prefix=key, max_depth=max_depth, depth=depth + 1))
        else:
            out.append((key, v))
    return out


def _pretty_value(v: Any) -> str:
    if v is None:
        return "-"
    if isinstance(v, bool):
        return "ON" if v else "OFF"
    if isinstance(v, (int, float)):
        if isinstance(v, float):
            return f"{v:.6g}"
        return str(v)
    if isinstance(v, list):
        if len(v) <= 10:
            return str(v)
        return f"[{', '.join(map(str, v[:10]))}, ...]"
    s = str(v)
    if len(s) > 80:
        return s[:80] + "…"
    return s


def _get_active_snapshot(user) -> Optional[AutoTradeSettingSnapshot]:
    return (
        AutoTradeSettingSnapshot.objects
        .filter(user=user, status="ACTIVE")
        .order_by("-id")
        .first()
    )


def _extract_active_breakout_core(active_snapshot: Optional[AutoTradeSettingSnapshot]) -> Dict[str, Any]:
    if active_snapshot is None:
        return {
            "id": None,
            "label": "-",
            "rr": None,
            "stop_pct_ui": None,
            "lookback_bars": None,
            "max_hold_min": None,
            "max_hold_bars": None,
            "daily_filter": "OFF",
            "daily_filter_label": "OFF（使わない）",
            "sma_days": None,
            "direction": "TREND_ONLY",
            "direction_label": "TREND_ONLY（トレンド方向だけ）",
        }

    sdict = active_snapshot.snapshot if isinstance(active_snapshot.snapshot, dict) else {}

    rr = _get_nested(sdict, ["lab", "BREAKOUT", "rr"], None)
    stop_pct_ui = _get_nested(sdict, ["lab", "BREAKOUT", "stop_pct_ui"], None)
    lookback_bars = _get_nested(sdict, ["lab", "BREAKOUT", "lookback_bars"], None)
    max_hold_min = _get_nested(sdict, ["lab", "BREAKOUT", "max_hold_min"], None)
    daily_filter = _get_nested(sdict, ["lab", "BREAKOUT", "daily_filter"], None)
    sma_days = _get_nested(sdict, ["lab", "BREAKOUT", "sma_days"], None)
    direction = _get_nested(sdict, ["lab", "BREAKOUT", "direction"], None)

    if rr is None:
        rr = sdict.get("rr_breakout")

    if stop_pct_ui is None:
        x = sdict.get("breakout_stop_pct")
        if x is not None:
            stop_pct_ui = _safe_float(x, 0.0) * 100.0

    if lookback_bars is None:
        lookback_bars = sdict.get("breakout_lookback_bars")

    if max_hold_min is None:
        max_hold_min = sdict.get("breakout_max_hold_min")

    if max_hold_min is None:
        max_hold_bars = sdict.get("breakout_max_hold_bars") or sdict.get("max_hold_bars")
        if max_hold_bars is not None:
            max_hold_min = _safe_int(max_hold_bars, 0) * 5

    max_hold_bars = None
    if max_hold_min is not None:
        max_hold_bars = max(1, int(round(_safe_float(max_hold_min, 0.0) / 5.0)))
    else:
        raw_bars = sdict.get("breakout_max_hold_bars") or sdict.get("max_hold_bars")
        if raw_bars is not None:
            max_hold_bars = max(1, _safe_int(raw_bars, 1))
            max_hold_min = int(max_hold_bars) * 5

    df = str(daily_filter) if daily_filter is not None else "OFF"
    if df not in ["OFF", "SMA"]:
        df = "OFF"

    dr = str(direction) if direction is not None else "TREND_ONLY"
    if dr not in ["TREND_ONLY", "BOTH"]:
        dr = "TREND_ONLY"

    df_label = "OFF（使わない）" if df == "OFF" else "SMA（日足の向きを揃える）"
    dr_label = "TREND_ONLY（トレンド方向だけ）" if dr == "TREND_ONLY" else "BOTH（両方向OK）"

    return {
        "id": active_snapshot.id,
        "label": active_snapshot.label,
        "rr": rr,
        "stop_pct_ui": stop_pct_ui,
        "lookback_bars": lookback_bars,
        "max_hold_min": max_hold_min,
        "max_hold_bars": max_hold_bars,
        "daily_filter": df,
        "daily_filter_label": df_label,
        "sma_days": sma_days,
        "direction": dr,
        "direction_label": dr_label,
    }


def build_active_summary_chips_for_template(*, active_snapshot: Optional[AutoTradeSettingSnapshot]) -> List[str]:
    if active_snapshot is None:
        return ["ACTIVE Snapshot がありません（まだ昇格していない可能性）"]

    core = _extract_active_breakout_core(active_snapshot)

    out: List[str] = []
    out.append(f"採用中の設定：ID {core['id']}（{core['label']}）")
    out.append(f"利確RR：{_pretty_value(core['rr'])}（利確幅＝損切り幅×RR）")
    out.append(f"損切り幅：{_pretty_value(core['stop_pct_ui'])}%（逆行したら損切り）")
    out.append(f"ブレイク判定：直近 {_pretty_value(core['lookback_bars'])} 本（5分足）")
    out.append(f"最大保有：{_pretty_value(core['max_hold_min'])} 分（持ちっぱなし防止）")
    out.append(f"日足フィルタ：{core['daily_filter_label']}")
    out.append(f"SMA日数：{_pretty_value(core['sma_days'])}")
    out.append(f"方向：{core['direction_label']}")
    return out


def build_active_detail_chips_for_template(*, active_snapshot: Optional[AutoTradeSettingSnapshot]) -> List[str]:
    if active_snapshot is None:
        return ["ACTIVE Snapshot がありません（まだ昇格していない可能性）"]

    try:
        sdict = active_snapshot.snapshot if isinstance(active_snapshot.snapshot, dict) else {}
    except Exception:
        sdict = {}

    exclude_prefixes = [
        "evidence",
        "auto_eval",
        "promote",
        "history",
        "debug",
        "raw",
        "logs",
    ]

    flat = _flatten_dict(sdict, max_depth=4)

    filtered: List[Tuple[str, Any]] = []
    for k, v in flat:
        k0 = str(k)
        if any(k0 == p or k0.startswith(p + ".") for p in exclude_prefixes):
            continue
        filtered.append((k0, v))

    priority_contains = [
        "lab.BREAKOUT",
        "rr",
        "stop_pct",
        "lookback",
        "max_hold",
        "daily_filter",
        "sma_days",
        "direction",
        "windows",
        "base_equity",
        "manual_eval",
    ]

    def _prio_key(k: str) -> Tuple[int, str]:
        for i, token in enumerate(priority_contains):
            if token.lower() in k.lower():
                return (i, k)
        return (999, k)

    filtered.sort(key=lambda kv: _prio_key(kv[0]))

    chips: List[str] = []
    for k, v in filtered:
        chips.append(f"{k}：{_pretty_value(v)}")

    if not chips:
        chips = ["ACTIVE Snapshot はありますが、表示できるキーがありません"]

    return chips


def _get_rules_dict(state: AutoTradeDailyState) -> Dict[str, Any]:
    return state.rules if isinstance(state.rules, dict) else {}


def _get_execution_mode_from_state(state: AutoTradeDailyState) -> str:
    rules = _get_rules_dict(state)
    mode = str(rules.get("execution_mode") or "PAPER").upper().strip()
    if mode not in ["PAPER", "LIVE"]:
        mode = "PAPER"
    return mode


def _fmt_dt_text(v: Any) -> str:
    if not v:
        return "-"
    try:
        dt = timezone.datetime.fromisoformat(str(v))
        if timezone.is_naive(dt):
            dt = timezone.make_aware(dt, timezone.get_current_timezone())
        return timezone.localtime(dt).strftime("%Y-%m-%d %H:%M")
    except Exception:
        s = str(v)
        if len(s) >= 16:
            return s[:16].replace("T", " ")
        return s


def _execution_local_dt(exe: AutoTradeExecution):
    dt = getattr(exe, "exit_at", None) or getattr(exe, "created_at", None)
    if dt is None:
        return None
    try:
        return timezone.localtime(dt)
    except Exception:
        return dt


def _execution_local_date(exe: AutoTradeExecution) -> Optional[dt_date]:
    dt = _execution_local_dt(exe)
    if dt is None:
        return None
    try:
        return dt.date()
    except Exception:
        return None


def _build_runtime_bucket(state: AutoTradeDailyState, *, mode: str) -> Dict[str, Any]:
    mode_up = str(mode or "PAPER").upper().strip()
    prefix = "live" if mode_up == "LIVE" else "paper"

    rules = _get_rules_dict(state)

    raw_open = rules.get(f"{prefix}_open_positions")
    raw_pending = rules.get(f"{prefix}_pending_orders")
    raw_logs = rules.get(f"{prefix}_logs")
    last_bar_ts = rules.get(f"{prefix}_last_bar_ts")

    raw_open = raw_open if isinstance(raw_open, dict) else {}
    raw_pending = raw_pending if isinstance(raw_pending, dict) else {}
    raw_logs = raw_logs if isinstance(raw_logs, list) else []

    open_positions: List[Dict[str, Any]] = []
    for ticker, p in raw_open.items():
        p = p if isinstance(p, dict) else {}
        open_positions.append({
            "ticker": str(ticker),
            "side": str(p.get("side") or "-"),
            "size": _safe_int(p.get("size"), 0),
            "entry_price": p.get("entry_price"),
            "stop_price": p.get("stop_price"),
            "take_price": p.get("take_price"),
            "entry_at_text": _fmt_dt_text(p.get("entry_at")),
            "expire_at_text": _fmt_dt_text(p.get("expire_at")),
        })

    pending_orders: List[Dict[str, Any]] = []
    for ticker, p in raw_pending.items():
        p = p if isinstance(p, dict) else {}
        pending_orders.append({
            "ticker": str(ticker),
            "side": str(p.get("side") or "-"),
            "created_at_text": _fmt_dt_text(p.get("created_at")),
            "based_on_bar_ts": str(p.get("based_on_bar_ts") or "-"),
        })

    logs: List[Dict[str, Any]] = []
    for x in reversed(raw_logs[-80:]):
        if isinstance(x, dict):
            logs.append({
                "ts": _fmt_dt_text(x.get("ts")),
                "msg": str(x.get("msg") or ""),
            })
        else:
            logs.append({
                "ts": "-",
                "msg": str(x),
            })

    return {
        "mode": mode_up,
        "open_positions": open_positions,
        "pending_orders": pending_orders,
        "logs": logs,
        "last_bar_ts": str(last_bar_ts or ""),
        "open_count": len(open_positions),
        "pending_count": len(pending_orders),
        "log_count": len(logs),
    }


def _calc_max_drawdown_yen(equity_points: List[int]) -> int:
    peak = None
    max_dd = 0
    for x in equity_points or []:
        try:
            v = int(x)
        except Exception:
            continue
        if peak is None or v > peak:
            peak = v
        if peak is not None:
            dd = peak - v
            if dd > max_dd:
                max_dd = dd
    return int(max_dd)


def _build_execution_stats(*, base_equity_yen: int, executions: List[AutoTradeExecution]) -> Dict[str, Any]:
    execs = list(executions or [])
    trades = len(execs)

    wins = 0
    losses = 0
    sum_win = 0
    sum_loss = 0
    pnl_sum = 0
    pnls: List[int] = []

    for e in execs:
        pnl = _safe_int(getattr(e, "pnl_yen", 0), 0)
        pnl_sum += pnl
        pnls.append(pnl)
        if pnl >= 0:
            wins += 1
            sum_win += pnl
        else:
            losses += 1
            sum_loss += pnl

    sum_loss_abs = abs(sum_loss)
    win_rate = (wins / trades) if trades > 0 else 0.0

    if sum_loss_abs > 0:
        pf_raw = float(sum_win) / float(sum_loss_abs)
        pf_text = f"{pf_raw:.3f}"
    else:
        pf_raw = 999.0 if sum_win > 0 else 0.0
        pf_text = "999.000" if sum_win > 0 else "0.000"

    eq = int(base_equity_yen or 1_000_000)
    equity_points = [eq]
    for pnl in pnls:
        eq += int(pnl)
        equity_points.append(eq)

    dd_yen = _calc_max_drawdown_yen(equity_points)
    dd_pct = (float(dd_yen) / float(base_equity_yen)) if base_equity_yen > 0 else 0.0

    return {
        "trades": int(trades),
        "wins": int(wins),
        "losses": int(losses),
        "win_rate_pct": round(_pct_100(win_rate), 1),
        "sum_win_yen": int(sum_win),
        "sum_loss_yen": int(sum_loss),
        "sum_loss_abs_yen": int(sum_loss_abs),
        "pnl_sum_yen": int(pnl_sum),
        "pf_raw": float(pf_raw),
        "pf": pf_text,
        "dd_yen": int(dd_yen),
        "dd_pct": round(_pct_100(dd_pct), 1),
    }


def _build_equity_curve(*, base_equity_yen: int, executions: List[AutoTradeExecution]) -> List[Dict[str, Any]]:
    execs = list(executions or [])
    eq = int(base_equity_yen or 1_000_000)

    out: List[Dict[str, Any]] = [{"ts": "start", "equity_yen": int(eq)}]

    for e in execs:
        pnl = _safe_int(getattr(e, "pnl_yen", 0), 0)
        eq += int(pnl)

        ts = "-"
        try:
            t = getattr(e, "exit_at", None) or getattr(e, "created_at", None)
            if t is not None:
                ts = timezone.localtime(t).strftime("%H:%M")
        except Exception:
            ts = "-"

        out.append({
            "ts": ts,
            "equity_yen": int(eq),
        })

    return out


def _build_mode_daily_rows(*, user, mode: str, limit_days: int = 30) -> List[Dict[str, Any]]:
    states = list(
        AutoTradeDailyState.objects.order_by("-date")[:max(10, int(limit_days))]
    )
    if not states:
        return []

    earliest_date = states[-1].date
    tz = timezone.get_current_timezone()
    start_dt = timezone.make_aware(datetime.combine(earliest_date, dt_time.min), tz)

    mode_up = str(mode or "PAPER").upper().strip()

    execs = list(
        AutoTradeExecution.objects.filter(
            user=user,
            mode=mode_up,
            exit_at__gte=start_dt,
        ).order_by("exit_at", "id")
    )

    exec_map: Dict[Any, List[AutoTradeExecution]] = {}
    for e in execs:
        base_dt = getattr(e, "exit_at", None) or getattr(e, "created_at", None)
        if base_dt is None:
            continue
        jst_day = timezone.localtime(base_dt).date()
        exec_map.setdefault(jst_day, []).append(e)

    rows: List[Dict[str, Any]] = []
    for state in states:
        day_execs = exec_map.get(state.date, [])

        start_equity_yen = int(_safe_int(state.equity_yen, 1_000_000) - _safe_int(state.pnl_day_yen, 0))
        if start_equity_yen <= 0:
            start_equity_yen = int(getattr(settings, "AUTOTRADE_BASE_EQUITY_YEN", 1_000_000))

        stats = _build_execution_stats(
            base_equity_yen=start_equity_yen,
            executions=day_execs,
        )

        gate_reason_first = "-"
        if str(state.gate_reason or "").strip():
            gate_reason_first = str(state.gate_reason or "").strip().splitlines()[0]

        rows.append({
            "date": state.date,
            "date_text": state.date.strftime("%Y-%m-%d"),
            "weekday_text": state.date.strftime("%a"),
            "gate_level": str(state.gate_level or "-"),
            "strategy": str(state.strategy or "-"),
            "trades": stats["trades"],
            "wins": stats["wins"],
            "losses": stats["losses"],
            "win_rate_pct": stats["win_rate_pct"],
            "pnl_sum_yen": stats["pnl_sum_yen"],
            "sum_win_yen": stats["sum_win_yen"],
            "sum_loss_yen": stats["sum_loss_yen"],
            "sum_loss_abs_yen": stats["sum_loss_abs_yen"],
            "pf": stats["pf"],
            "pf_raw": stats["pf_raw"],
            "dd_yen": stats["dd_yen"],
            "dd_pct": stats["dd_pct"],
            "gate_reason_first": gate_reason_first,
        })

    return rows


def _build_demo_daily_rows(*, user, limit_days: int = 30) -> List[Dict[str, Any]]:
    return _build_mode_daily_rows(user=user, mode="PAPER", limit_days=limit_days)


def _judge_daily_window(summary: Dict[str, Any], required_days: int) -> Tuple[str, str]:
    days = _safe_int(summary.get("days"), 0)
    trades = _safe_int(summary.get("trades"), 0)
    pnl = _safe_int(summary.get("pnl_sum_yen"), 0)
    pf_raw = _safe_float(summary.get("pf_raw"), 0.0)
    plus_days = _safe_int(summary.get("plus_days"), 0)
    minus_days = _safe_int(summary.get("minus_days"), 0)

    if days < required_days:
        return ("データ不足", "is-warn")
    if trades == 0:
        return ("取引なし", "is-warn")
    if pnl > 0 and pf_raw >= 1.10 and plus_days >= minus_days:
        return ("良好", "is-ok")
    if pnl >= 0 and pf_raw >= 1.00:
        return ("様子見", "is-warn")
    return ("要見直し", "is-bad")


def _build_demo_window_summary(rows: List[Dict[str, Any]], *, window: int) -> Dict[str, Any]:
    picked = list(rows[:max(0, int(window))])
    if not picked:
        judge_text, judge_class = _judge_daily_window({"days": 0}, int(window))
        return {
            "label": f"直近{window}営業日",
            "days": 0,
            "active_days": 0,
            "plus_days": 0,
            "minus_days": 0,
            "flat_days": 0,
            "trades": 0,
            "wins": 0,
            "losses": 0,
            "win_rate_pct": 0.0,
            "pnl_sum_yen": 0,
            "sum_win_yen": 0,
            "sum_loss_abs_yen": 0,
            "pf": "0.000",
            "pf_raw": 0.0,
            "dd_yen": 0,
            "judge_text": judge_text,
            "judge_class": judge_class,
        }

    trades = sum(_safe_int(x.get("trades"), 0) for x in picked)
    wins = sum(_safe_int(x.get("wins"), 0) for x in picked)
    losses = sum(_safe_int(x.get("losses"), 0) for x in picked)
    pnl_sum_yen = sum(_safe_int(x.get("pnl_sum_yen"), 0) for x in picked)
    sum_win_yen = sum(_safe_int(x.get("sum_win_yen"), 0) for x in picked)
    sum_loss_abs_yen = sum(_safe_int(x.get("sum_loss_abs_yen"), 0) for x in picked)

    days = len(picked)
    active_days = sum(1 for x in picked if _safe_int(x.get("trades"), 0) > 0)
    plus_days = sum(1 for x in picked if _safe_int(x.get("pnl_sum_yen"), 0) > 0)
    minus_days = sum(1 for x in picked if _safe_int(x.get("pnl_sum_yen"), 0) < 0)
    flat_days = days - plus_days - minus_days

    win_rate_pct = round((wins / trades) * 100.0, 1) if trades > 0 else 0.0

    if sum_loss_abs_yen > 0:
        pf_raw = float(sum_win_yen) / float(sum_loss_abs_yen)
        pf_text = f"{pf_raw:.3f}"
    else:
        pf_raw = 999.0 if sum_win_yen > 0 else 0.0
        pf_text = "999.000" if sum_win_yen > 0 else "0.000"

    cumulative = 0
    points = [0]
    for row in reversed(picked):
        cumulative += _safe_int(row.get("pnl_sum_yen"), 0)
        points.append(int(cumulative))
    dd_yen = _calc_max_drawdown_yen(points)

    summary = {
        "label": f"直近{window}営業日",
        "days": int(days),
        "active_days": int(active_days),
        "plus_days": int(plus_days),
        "minus_days": int(minus_days),
        "flat_days": int(flat_days),
        "trades": int(trades),
        "wins": int(wins),
        "losses": int(losses),
        "win_rate_pct": float(win_rate_pct),
        "pnl_sum_yen": int(pnl_sum_yen),
        "sum_win_yen": int(sum_win_yen),
        "sum_loss_abs_yen": int(sum_loss_abs_yen),
        "pf": pf_text,
        "pf_raw": float(pf_raw),
        "dd_yen": int(dd_yen),
    }

    judge_text, judge_class = _judge_daily_window(summary, int(window))
    summary["judge_text"] = judge_text
    summary["judge_class"] = judge_class
    return summary


def _build_mode_dashboard_summary(*, user, mode: str, today: dt_date) -> Dict[str, Any]:
    mode_up = str(mode or "PAPER").upper().strip()
    base_equity_yen = int(getattr(settings, "AUTOTRADE_BASE_EQUITY_YEN", 1_000_000))

    all_execs = list(
        AutoTradeExecution.objects
        .filter(user=user, mode=mode_up)
        .exclude(mode="BACKTEST")
        .order_by("exit_at", "id")
    )

    today_execs = list(
        AutoTradeExecution.objects
        .filter(user=user, mode=mode_up, created_at__date=today)
        .exclude(mode="BACKTEST")
        .order_by("exit_at", "id")
    )

    cumulative_stats = _build_execution_stats(
        base_equity_yen=base_equity_yen,
        executions=all_execs,
    )
    today_stats = _build_execution_stats(
        base_equity_yen=base_equity_yen,
        executions=today_execs,
    )

    start_text = "-"
    last_updated_text = "-"
    if all_execs:
        first_dt = _execution_local_dt(all_execs[0])
        last_dt = _execution_local_dt(all_execs[-1])
        if first_dt is not None:
            start_text = first_dt.strftime("%Y-%m-%d")
        if last_dt is not None:
            last_updated_text = last_dt.strftime("%Y-%m-%d")

    rows = _build_mode_daily_rows(user=user, mode=mode_up, limit_days=30)
    window_5 = _build_demo_window_summary(rows, window=5)
    window_10 = _build_demo_window_summary(rows, window=10)

    cumulative_equity_yen = int(base_equity_yen + _safe_int(cumulative_stats.get("pnl_sum_yen"), 0))

    return {
        "mode": mode_up,
        "mode_label": _mode_label(mode_up),
        "base_equity_yen": int(base_equity_yen),
        "cumulative_equity_yen": int(cumulative_equity_yen),
        "cumulative_pnl_yen": int(cumulative_stats.get("pnl_sum_yen", 0)),
        "cumulative_pf": str(cumulative_stats.get("pf", "0.000")),
        "cumulative_pf_raw": float(cumulative_stats.get("pf_raw", 0.0)),
        "cumulative_win_rate_pct": float(cumulative_stats.get("win_rate_pct", 0.0)),
        "cumulative_trades": int(cumulative_stats.get("trades", 0)),
        "today_pnl_yen": int(today_stats.get("pnl_sum_yen", 0)),
        "today_pf": str(today_stats.get("pf", "0.000")),
        "today_win_rate_pct": float(today_stats.get("win_rate_pct", 0.0)),
        "today_trades": int(today_stats.get("trades", 0)),
        "start_text": start_text,
        "last_updated_text": last_updated_text,
        "window_5": window_5,
        "window_10": window_10,
    }


def _build_runtime_view(*, state: AutoTradeDailyState, active_snapshot: Optional[AutoTradeSettingSnapshot]) -> Dict[str, Any]:
    rules = _get_rules_dict(state)
    runtime = rules.get("runtime") if isinstance(rules.get("runtime"), dict) else {}
    risk = rules.get("risk") if isinstance(rules.get("risk"), dict) else {}
    limits = rules.get("limits") if isinstance(rules.get("limits"), dict) else {}
    time_box = rules.get("time") if isinstance(rules.get("time"), dict) else {}
    notes = [str(x) for x in (rules.get("notes") or []) if str(x).strip()]
    active_core = _extract_active_breakout_core(active_snapshot)

    original_gate_level = str(runtime.get("original_gate_level") or state.gate_level or "STOP").upper().strip()
    effective_gate_level = str(runtime.get("effective_gate_level") or state.gate_level or "STOP").upper().strip()

    allow_long = _safe_bool(runtime.get("allow_long"), True)
    allow_short = _safe_bool(runtime.get("allow_short"), True)

    trade_loss_pct = _safe_float(
        risk.get("trade_loss_pct"),
        _safe_float(getattr(settings, "AUTOTRADE_RISK_TRADE_PCT", 0.0015), 0.0015),
    )
    day_loss_pct = _safe_float(
        risk.get("day_loss_pct"),
        _safe_float(getattr(settings, "AUTOTRADE_RISK_DAY_PCT", 0.01), 0.01),
    )
    trade_loss_yen = _safe_int(
        risk.get("trade_loss_yen"),
        int(round(int(getattr(settings, "AUTOTRADE_BASE_EQUITY_YEN", 1_000_000)) * trade_loss_pct)),
    )
    day_loss_yen = _safe_int(
        risk.get("day_loss_yen"),
        int(round(int(getattr(settings, "AUTOTRADE_BASE_EQUITY_YEN", 1_000_000)) * day_loss_pct)),
    )

    default_positions = 2 if effective_gate_level == "FULL" else 1 if effective_gate_level == "LIGHT" else 0
    default_trades = 6 if effective_gate_level == "FULL" else 3 if effective_gate_level == "LIGHT" else 0

    max_positions = _safe_int(
        runtime.get("max_positions_override"),
        _safe_int(risk.get("max_positions"), default_positions),
    )
    max_trades = _safe_int(
        runtime.get("max_trades_override"),
        _safe_int(limits.get("max_trades_per_day"), default_trades),
    )

    active_max_hold_bars = _safe_int(active_core.get("max_hold_bars"), 0)
    max_hold_bars = _safe_int(
        runtime.get("max_hold_bars_cap"),
        active_max_hold_bars if active_max_hold_bars > 0 else 0,
    )

    diagnosis_tags = [str(x) for x in (runtime.get("diagnosis_tags") or []) if str(x).strip()]
    regime_warning = str(runtime.get("regime_warning") or "").strip()

    runtime_notes: List[str] = []
    for x in notes:
        if any(key in x for key in ["直近", "ロング", "ショート", "上限", "短縮", "停止", "OVERTRADING", "TIME_BIASED", "LONG_WEAK"]):
            runtime_notes.append(x)

    if not runtime_notes:
        runtime_notes = notes[-3:]

    if allow_long and allow_short:
        direction_text = "ロング/ショート可"
    elif allow_long and not allow_short:
        direction_text = "ロングのみ"
    elif not allow_long and allow_short:
        direction_text = "ショートのみ"
    else:
        direction_text = "新規停止"

    if effective_gate_level == "STOP":
        summary_line = "今日は停止です。新規建てを行いません。"
    else:
        summary_line = f"今日は {effective_gate_level}。{direction_text}、最大 {max_trades} 回"
        if max_hold_bars > 0:
            summary_line += f"、最大保有 {max_hold_bars} 本"

    return {
        "original_gate_level": original_gate_level,
        "original_gate_class": _gate_class(original_gate_level),
        "effective_gate_level": effective_gate_level,
        "effective_gate_class": _gate_class(effective_gate_level),
        "allow_long": bool(allow_long),
        "allow_long_label": "ON" if allow_long else "OFF",
        "allow_short": bool(allow_short),
        "allow_short_label": "ON" if allow_short else "OFF",
        "max_positions": int(max_positions),
        "max_trades": int(max_trades),
        "max_hold_bars": int(max_hold_bars),
        "trade_loss_pct": float(trade_loss_pct),
        "trade_loss_yen": int(trade_loss_yen),
        "day_loss_pct": float(day_loss_pct),
        "day_loss_yen": int(day_loss_yen),
        "session_start": str(time_box.get("session_start") or getattr(settings, "AUTOTRADE_SESSION_START", "09:00")),
        "session_end": str(time_box.get("session_end") or getattr(settings, "AUTOTRADE_SESSION_END", "15:00")),
        "force_close": str(time_box.get("force_close") or getattr(settings, "AUTOTRADE_FORCE_CLOSE", "15:25")),
        "regime_warning": regime_warning,
        "diagnosis_tags": diagnosis_tags,
        "runtime_notes": runtime_notes,
        "summary_line": summary_line,
    }


@login_required
def dashboard(request: HttpRequest):
    today = timezone.localdate()
    state, _ = AutoTradeDailyState.objects.get_or_create(date=today)

    active_snapshot = _get_active_snapshot(request.user)
    current_mode = _get_execution_mode_from_state(state)
    current_mode_label = _mode_label(current_mode)

    runtime_summary = _build_runtime_bucket(state, mode=current_mode)
    runtime_view = _build_runtime_view(state=state, active_snapshot=active_snapshot)
    mode_dashboard = _build_mode_dashboard_summary(user=request.user, mode=current_mode, today=today)

    gate_reason_lines = [x for x in str(state.gate_reason or "").splitlines() if str(x).strip()]

    ctx = {
        "state": state,
        "current_mode": current_mode,
        "current_mode_label": current_mode_label,
        "runtime_summary": runtime_summary,
        "runtime_view": runtime_view,
        "mode_dashboard": mode_dashboard,
        "result_cards": build_result_cards_for_template(state),
        "thresholds": build_thresholds_for_template(),
        "active_summary_chips": build_active_summary_chips_for_template(active_snapshot=active_snapshot),
        "active_detail_chips": build_active_detail_chips_for_template(active_snapshot=active_snapshot),
        "gate_reason_lines": gate_reason_lines,
    }
    return render(request, "autotrade/dashboard.html", ctx)


@login_required
def execution_report(request: HttpRequest):
    today = timezone.localdate()
    state, _ = AutoTradeDailyState.objects.get_or_create(date=today)

    current_mode = _get_execution_mode_from_state(state)
    selected_mode = str(request.GET.get("mode") or current_mode).upper().strip()
    if selected_mode not in ["PAPER", "LIVE", "ALL"]:
        selected_mode = current_mode

    qs = (
        AutoTradeExecution.objects.filter(
            user=request.user,
            created_at__date=today,
        )
        .exclude(mode="BACKTEST")
        .order_by("exit_at", "id")
    )

    if selected_mode in ["PAPER", "LIVE"]:
        qs = qs.filter(mode=selected_mode)

    executions_today = list(qs)

    start_equity_yen = int(_safe_int(state.equity_yen, 1_000_000) - _safe_int(state.pnl_day_yen, 0))
    if start_equity_yen <= 0:
        start_equity_yen = int(_safe_int(state.equity_yen, 1_000_000)) or 1_000_000

    report_stats = _build_execution_stats(
        base_equity_yen=start_equity_yen,
        executions=executions_today,
    )

    equity_curve = _build_equity_curve(
        base_equity_yen=start_equity_yen,
        executions=executions_today,
    )

    runtime_mode = selected_mode if selected_mode in ["PAPER", "LIVE"] else current_mode
    runtime_bucket = _build_runtime_bucket(state, mode=runtime_mode)

    ctx = {
        "state": state,
        "current_mode": current_mode,
        "selected_mode": selected_mode,
        "report_stats": report_stats,
        "executions_today": executions_today,
        "runtime_bucket": runtime_bucket,
        "equity_curve": equity_curve,
    }
    return render(request, "autotrade/execution_report.html", ctx)


@login_required
def demo_daily_history(request: HttpRequest):
    selected_mode = str(request.GET.get("mode") or "PAPER").upper().strip()
    if selected_mode not in ["PAPER", "LIVE"]:
        selected_mode = "PAPER"

    rows = _build_mode_daily_rows(user=request.user, mode=selected_mode, limit_days=30)
    summary_5 = _build_demo_window_summary(rows, window=5)
    summary_10 = _build_demo_window_summary(rows, window=10)

    ctx = {
        "rows": rows,
        "summary_5": summary_5,
        "summary_10": summary_10,
        "selected_mode": selected_mode,
        "selected_mode_label": _mode_label(selected_mode),
    }
    return render(request, "autotrade/demo_daily_history.html", ctx)