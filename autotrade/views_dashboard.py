"""
[FILE] autotrade/views_dashboard.py
[PATH] <project_root>/autotrade/views_dashboard.py

このファイルは何？
- AutoTrade のダッシュボード表示（iPhone 1画面）用の View と整形関数です。
- DB上の「DailyState（今日の状態）」＋「ACTIVE Snapshot（今日の設定）」＋「Execution（今日の事実ログ）」をまとめてテンプレへ渡します。

今回の修正：
- execution_report() を追加
- dashboard では現在モードの要点だけ表示
- 専用結果ページで、PAPER/LIVE/ALL の結果を見やすく出せるようにする
- ★追加修正：エクイティ推移（equity_curve）が専用結果ページに表示されるように、
  _build_equity_curve() を追加し、execution_report() の ctx に渡す
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest
from django.shortcuts import render
from django.utils import timezone

from .models import AutoTradeDailyState, AutoTradeSettingSnapshot
from .views_utils import get_nested_dict

from autotrade.models_backtest import AutoTradeExecution
from autotrade.services.backtest.gate import GATE_THRESHOLDS


def _safe_float(x: Any, default: float = 0.0) -> float:
    try:
        return float(x)
    except Exception:
        return float(default)


def _safe_int(x: Any, default: int = 0) -> int:
    try:
        return int(x)
    except Exception:
        return int(default)


def _pct_100(x01: Any) -> float:
    v = _safe_float(x01, 0.0)
    return float(v * 100.0)


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
        windows = [20, 60]

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


def _get_nested(d: Dict[str, Any], keys: List[str], default: Any = None) -> Any:
    cur: Any = d
    for k in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(k)
    return cur if cur is not None else default


def build_active_summary_chips_for_template(*, active_snapshot: Optional[AutoTradeSettingSnapshot]) -> List[str]:
    if active_snapshot is None:
        return ["ACTIVE Snapshot がありません（まだ昇格していない可能性）"]

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

    df = str(daily_filter) if daily_filter is not None else "OFF"
    if df not in ["OFF", "SMA"]:
        df = "OFF"

    dr = str(direction) if direction is not None else "TREND_ONLY"
    if dr not in ["TREND_ONLY", "BOTH"]:
        dr = "TREND_ONLY"

    df_label = "OFF（使わない）" if df == "OFF" else "SMA（日足の向きを揃える）"
    dr_label = "TREND_ONLY（トレンド方向だけ）" if dr == "TREND_ONLY" else "BOTH（両方向OK）"

    out: List[str] = []
    out.append(f"採用中の設定：ID {active_snapshot.id}（{active_snapshot.label}）")
    out.append(f"利確RR：{_pretty_value(rr)}（利確幅＝損切り幅×RR）")
    out.append(f"損切り幅：{_pretty_value(stop_pct_ui)}%（逆行したら損切り）")
    out.append(f"ブレイク判定：直近 {_pretty_value(lookback_bars)} 本（5分足）")
    out.append(f"最大保有：{_pretty_value(max_hold_min)} 分（持ちっぱなし防止）")
    out.append(f"日足フィルタ：{df_label}")
    out.append(f"SMA日数：{_pretty_value(sma_days)}（例：20/50）")
    out.append(f"方向：{dr_label}")
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
        pf = float(sum_win) / float(sum_loss_abs)
    else:
        pf = 999.0 if sum_win > 0 else 0.0

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
        "sum_loss_abs_yen": int(sum_loss_abs),
        "pnl_sum_yen": int(pnl_sum),
        "pf": f"{pf:.3f}" if pf else f"{pf:.2f}",
        "dd_yen": int(dd_yen),
        "dd_pct": round(_pct_100(dd_pct), 1),
    }


def _build_equity_curve(*, base_equity_yen: int, executions: List[AutoTradeExecution]) -> List[Dict[str, Any]]:
    """
    簡易エクイティ推移（時刻 → 総資産）。
    - base_equity から pnl を順に加算
    - 表示時刻は exit_at 優先、無ければ created_at
    """
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


@login_required
def dashboard(request: HttpRequest):
    today = timezone.localdate()
    state, _ = AutoTradeDailyState.objects.get_or_create(date=today)

    active_snapshot = _get_active_snapshot(request.user)
    current_mode = _get_execution_mode_from_state(state)

    executions_today_all = list(
        AutoTradeExecution.objects.filter(
            user=request.user,
            created_at__date=today,
        )
        .exclude(mode="BACKTEST")
        .order_by("exit_at", "id")
    )

    executions_current_mode = [
        e for e in executions_today_all
        if str(getattr(e, "mode", "")).upper() == current_mode
    ]

    start_equity_yen = int(_safe_int(state.equity_yen, 1_000_000) - _safe_int(state.pnl_day_yen, 0))
    if start_equity_yen <= 0:
        start_equity_yen = int(_safe_int(state.equity_yen, 1_000_000)) or 1_000_000

    current_mode_stats = _build_execution_stats(
        base_equity_yen=start_equity_yen,
        executions=executions_current_mode,
    )

    runtime_summary = _build_runtime_bucket(state, mode=current_mode)

    ctx = {
        "state": state,
        "result_cards": build_result_cards_for_template(state),
        "thresholds": build_thresholds_for_template(),
        "active_summary_chips": build_active_summary_chips_for_template(active_snapshot=active_snapshot),
        "active_detail_chips": build_active_detail_chips_for_template(active_snapshot=active_snapshot),
        "current_mode": current_mode,
        "current_mode_stats": current_mode_stats,
        "runtime_summary": runtime_summary,
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