# =========================================================
# [FILE] autotrade/jobs/intraday_trade.py
# [PATH] <project_root>/autotrade/jobs/intraday_trade.py
#
# このファイルは何？
# - 場中（毎分）に動く「デモトレード（PAPER）執行ジョブ」です。
#
# 今回の変更：
# - dashboard からの execution_mode（PAPER / LIVE）を読む
# - ただし LIVE は現段階では安全装置で停止し、live_logs に理由だけ残す
# - fake LIVE（本番のふりをしたPAPER記録）を絶対に作らない
# - recent diagnosis による実運用調整（LIGHT/STOP落とし、方向制限、回数制限、保有短縮）を反映
# =========================================================

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple
from datetime import date, datetime, time as dt_time, timedelta
import os
import time

from django.conf import settings
from django.db import transaction
from django.db.utils import OperationalError
from django.utils import timezone

from autotrade.models import AutoTradeDailyState, AutoTradeSettingSnapshot
from autotrade.models_backtest import AutoTradeExecution
from autotrade.services.common.guards import is_emergency_stopped
from autotrade.services.backtest.data_fetcher import fetch_5m
from autotrade.services.common.cron_lock import cron_file_lock

from autotrade.services.backtest.engine_breakout import (
    _get_stop_pct_breakout_from_snapshot,
    _get_lookback_bars_from_snapshot,
    _get_max_hold_bars_from_snapshot,
    _get_daily_filter_params_from_snapshot,
    _build_daily_trend_map,
    _ensure_aware,
)


def _db_retry(func, *, tries: int = 6, sleep_sec: float = 0.25):
    last = None
    for _ in range(max(1, int(tries))):
        try:
            return func()
        except OperationalError as e:
            msg = str(e).lower()
            if "database is locked" not in msg:
                raise
            last = e
            time.sleep(float(sleep_sec))
    if last:
        raise last


def _now_jst() -> datetime:
    return timezone.localtime(timezone.now())


def _parse_hhmm(x: str, default: str) -> dt_time:
    s = str(x or "").strip() or default
    hh, mm = s.split(":")
    return dt_time(int(hh), int(mm))


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


def _get_active_snapshot() -> Optional[AutoTradeSettingSnapshot]:
    return (
        AutoTradeSettingSnapshot.objects
        .filter(status="ACTIVE")
        .order_by("-id")
        .first()
    )


def _get_today_picks_from_state(state: AutoTradeDailyState) -> List[str]:
    uni = state.universe if isinstance(state.universe, dict) else {}
    picks = [x.get("ticker") for x in (uni.get("picks") or []) if isinstance(x, dict) and x.get("ticker")]
    picks = [str(x).strip() for x in (picks or []) if str(x).strip()]
    return picks


def _gate_limits(gate_level: str) -> Tuple[int, int]:
    g = str(gate_level or "STOP").upper().strip()
    if g == "FULL":
        return (
            int(getattr(settings, "AUTOTRADE_MAX_POSITIONS_FULL", 2)),
            int(getattr(settings, "AUTOTRADE_MAX_TRADES_FULL", 6)),
        )
    if g == "LIGHT":
        return (
            int(getattr(settings, "AUTOTRADE_MAX_POSITIONS_LIGHT", 1)),
            int(getattr(settings, "AUTOTRADE_MAX_TRADES_LIGHT", 3)),
        )
    return (0, 0)


def _calc_shares(*, equity_yen: int, price: float, stop_pct: float) -> int:
    risk_pct = float(getattr(settings, "AUTOTRADE_RISK_TRADE_PCT", 0.0015))
    risk_yen = float(equity_yen) * risk_pct
    stop_yen_per_share = float(price) * float(stop_pct)
    if stop_yen_per_share <= 0:
        return 0
    shares = int((risk_yen / stop_yen_per_share) // 100) * 100
    return int(max(0, shares))


def _trend_allows(*, snapshot: AutoTradeSettingSnapshot, ticker: str, entry_dt: datetime, side: str) -> bool:
    dfp = _get_daily_filter_params_from_snapshot(snapshot)
    daily_filter = str(dfp.get("daily_filter") or "OFF").upper()
    direction = str(dfp.get("direction") or "TREND_ONLY").upper()
    sma_days = int(dfp.get("sma_days") or 20)

    if daily_filter != "SMA":
        return True
    if direction == "BOTH":
        return True

    trend_map = _build_daily_trend_map(ticker=ticker, sma_days=sma_days)
    if not trend_map:
        return True

    try:
        d = timezone.localtime(entry_dt).date().isoformat()
    except Exception:
        d = str(entry_dt)[:10]

    tr = trend_map.get(d)
    if tr not in ["UP", "DOWN"]:
        return True

    s = str(side or "").upper()
    if tr == "UP" and s == "LONG":
        return True
    if tr == "DOWN" and s == "SHORT":
        return True
    return False


def _get_rules_dict(state: AutoTradeDailyState) -> Dict[str, Any]:
    return state.rules if isinstance(state.rules, dict) else {}


def _set_rules_dict(state: AutoTradeDailyState, rules: Dict[str, Any]) -> None:
    state.rules = rules


def _get_execution_mode_from_state(state: AutoTradeDailyState) -> str:
    rules = _get_rules_dict(state)
    mode = str(rules.get("execution_mode") or "PAPER").upper().strip()
    if mode not in ["PAPER", "LIVE"]:
        mode = "PAPER"
    return mode


def _get_runtime_dict(state: AutoTradeDailyState) -> Dict[str, Any]:
    rules = _get_rules_dict(state)
    rt = rules.get("runtime")
    return rt if isinstance(rt, dict) else {}


def _get_effective_gate_level(state: AutoTradeDailyState) -> str:
    rt = _get_runtime_dict(state)
    lv = str(rt.get("effective_gate_level") or state.gate_level or "STOP").upper().strip()
    if lv not in ["FULL", "LIGHT", "STOP"]:
        lv = "STOP"
    return lv


def _get_runtime_limits(state: AutoTradeDailyState, gate_level: str) -> Tuple[int, int]:
    rules = _get_rules_dict(state)

    risk = rules.get("risk") if isinstance(rules.get("risk"), dict) else {}
    limits = rules.get("limits") if isinstance(rules.get("limits"), dict) else {}

    pos_from_rules = risk.get("max_positions")
    trades_from_rules = limits.get("max_trades_per_day")

    if pos_from_rules is not None and trades_from_rules is not None:
        return (
            max(0, _safe_int(pos_from_rules, 0)),
            max(0, _safe_int(trades_from_rules, 0)),
        )

    return _gate_limits(gate_level)


def _runtime_allows_side(state: AutoTradeDailyState, side: str) -> bool:
    rt = _get_runtime_dict(state)
    s = str(side or "").upper().strip()

    allow_long = bool(rt.get("allow_long", True))
    allow_short = bool(rt.get("allow_short", True))

    if s == "LONG":
        return allow_long
    if s == "SHORT":
        return allow_short
    return False


def _runtime_max_hold_bars_cap(state: AutoTradeDailyState) -> Optional[int]:
    rt = _get_runtime_dict(state)
    x = rt.get("max_hold_bars_cap")
    if x is None:
        return None
    v = _safe_int(x, 0)
    return v if v > 0 else None


def _append_mode_log(state: AutoTradeDailyState, *, mode: str, msg: str) -> None:
    rules = _get_rules_dict(state)
    prefix = "live" if str(mode).upper() == "LIVE" else "paper"
    key = f"{prefix}_logs"
    logs = rules.get(key)
    if not isinstance(logs, list):
        logs = []
    logs.append({"ts": timezone.localtime(timezone.now()).isoformat(), "msg": str(msg)})
    rules[key] = logs[-80:]
    _set_rules_dict(state, rules)


def _get_open_positions(state: AutoTradeDailyState) -> Dict[str, Any]:
    rules = _get_rules_dict(state)
    pos = rules.get("paper_open_positions")
    return pos if isinstance(pos, dict) else {}


def _set_open_positions(state: AutoTradeDailyState, pos: Dict[str, Any]) -> None:
    rules = _get_rules_dict(state)
    rules["paper_open_positions"] = pos
    _set_rules_dict(state, rules)


def _get_pending_orders(state: AutoTradeDailyState) -> Dict[str, Any]:
    rules = _get_rules_dict(state)
    od = rules.get("paper_pending_orders")
    return od if isinstance(od, dict) else {}


def _set_pending_orders(state: AutoTradeDailyState, od: Dict[str, Any]) -> None:
    rules = _get_rules_dict(state)
    rules["paper_pending_orders"] = od
    _set_rules_dict(state, rules)


def _get_last_bar_ts(state: AutoTradeDailyState) -> str:
    rules = _get_rules_dict(state)
    return str(rules.get("paper_last_bar_ts") or "")


def _set_last_bar_ts(state: AutoTradeDailyState, bar_ts: str) -> None:
    rules = _get_rules_dict(state)
    rules["paper_last_bar_ts"] = str(bar_ts)
    _set_rules_dict(state, rules)


def _is_force_close_time(now: datetime) -> bool:
    fc = _parse_hhmm(getattr(settings, "AUTOTRADE_FORCE_CLOSE", "15:25"), "15:25")
    return now.time() >= fc


def _forbid_new_entries_by_time(now: datetime) -> bool:
    end = _parse_hhmm(getattr(settings, "AUTOTRADE_SESSION_END", "15:00"), "15:00")
    return now.time() >= end


def _allow_hold_past_end() -> bool:
    return bool(getattr(settings, "AUTOTRADE_ALLOW_HOLD_PAST_SESSION_END", True))


def _today_trade_count(user, today: date) -> int:
    return (
        AutoTradeExecution.objects
        .filter(user=user, created_at__date=today)
        .exclude(mode="BACKTEST")
        .count()
    )


def _fetch_last_two_bars(ticker: str) -> Optional[Tuple[Dict[str, Any], Dict[str, Any]]]:
    df = fetch_5m(ticker, prefer_period="5d")
    if df is None or getattr(df, "empty", True):
        return None
    if len(df) < 3:
        return None

    try:
        prev_row = df.iloc[-2]
        cur_row = df.iloc[-1]
        prev_idx = df.index[-2]
        cur_idx = df.index[-1]

        prev_dt = prev_idx.to_pydatetime() if hasattr(prev_idx, "to_pydatetime") else prev_idx
        cur_dt = cur_idx.to_pydatetime() if hasattr(cur_idx, "to_pydatetime") else cur_idx
        prev_dt = _ensure_aware(prev_dt)
        cur_dt = _ensure_aware(cur_dt)

        prev = {
            "ts": timezone.localtime(prev_dt).isoformat(),
            "dt": prev_dt,
            "open": float(prev_row["open"]),
            "high": float(prev_row["high"]),
            "low": float(prev_row["low"]),
            "close": float(prev_row["close"]),
        }
        cur = {
            "ts": timezone.localtime(cur_dt).isoformat(),
            "dt": cur_dt,
            "open": float(cur_row["open"]),
            "high": float(cur_row["high"]),
            "low": float(cur_row["low"]),
            "close": float(cur_row["close"]),
        }
        return (prev, cur)
    except Exception:
        return None


def _compute_breakout_signal_from_prev_bar(
    *,
    ticker: str,
    lookback_bars: int,
) -> Optional[str]:
    df = fetch_5m(ticker, prefer_period="5d")
    if df is None or getattr(df, "empty", True):
        return None
    if len(df) < (lookback_bars + 10):
        return None

    lb = int(max(2, min(60, lookback_bars)))

    try:
        highs = df["high"].values
        lows = df["low"].values
        closes = df["close"].values
    except Exception:
        return None

    i_prev = len(df) - 2
    i_end = i_prev
    i_start = i_end - lb

    if i_start < 2:
        return None

    hh = max(highs[i_start:i_end])
    ll = min(lows[i_start:i_end])
    price = float(closes[i_prev])

    if price > float(hh):
        return "LONG"
    if price < float(ll):
        return "SHORT"
    return None


@transaction.atomic
def run():
    lock_dir = str(getattr(settings, "AUTOTRADE_CRON_LOCK_DIR", "/tmp"))
    lock_path = os.path.join(lock_dir, "autotrade_intraday.lock")
    with cron_file_lock(lock_path) as acquired:
        if not acquired:
            return {"ok": True, "skipped": True, "reason": "locked_by_other_job"}

        today = date.today()
        now = _now_jst()

        def _load_state():
            return AutoTradeDailyState.objects.get_or_create(date=today)

        state, _ = _db_retry(_load_state)

        if is_emergency_stopped(state):
            return {"ok": True, "skipped": True, "reason": "emergency_stop"}

        exec_mode = _get_execution_mode_from_state(state)

        # LIVE は安全停止
        if exec_mode == "LIVE":
            _append_mode_log(
                state,
                mode="LIVE",
                msg="LIVEモードが選択されていますが、現段階では証券会社API未接続のため新規発注は行いません。"
            )
            state.updated_at = timezone.now()
            _db_retry(lambda: state.save(update_fields=["rules", "updated_at"]))
            return {"ok": True, "skipped": True, "reason": "live_mode_safe_stop"}

        active = _get_active_snapshot()
        if active is None:
            _append_mode_log(state, mode="PAPER", msg="ACTIVE Snapshot が無いので、PAPERは動かしません。")
            state.updated_at = timezone.now()
            _db_retry(lambda: state.save(update_fields=["rules", "updated_at"]))
            return {"ok": True, "skipped": True, "reason": "no_active"}

        user = active.user

        picks = _get_today_picks_from_state(state)
        if not picks:
            _append_mode_log(state, mode="PAPER", msg="今日のpicksが無い（state.universe）ため、PAPERは見送ります。")
            state.updated_at = timezone.now()
            _db_retry(lambda: state.save(update_fields=["rules", "updated_at"]))
            return {"ok": True, "skipped": True, "reason": "no_picks"}

        gate_level = _get_effective_gate_level(state)
        max_pos, max_trades = _get_runtime_limits(state, gate_level)

        stop_pct = float(_get_stop_pct_breakout_from_snapshot(active))
        lookback_bars = int(_get_lookback_bars_from_snapshot(active))
        max_hold_bars = int(_get_max_hold_bars_from_snapshot(active))

        hold_cap = _runtime_max_hold_bars_cap(state)
        if hold_cap is not None:
            max_hold_bars = min(int(max_hold_bars), int(hold_cap))
            if max_hold_bars < 1:
                max_hold_bars = 1

        rr = _safe_float((active.snapshot or {}).get("rr_breakout"), float(getattr(settings, "AUTOTRADE_RR_BREAKOUT", 2.0)))
        if rr <= 0.1:
            rr = float(getattr(settings, "AUTOTRADE_RR_BREAKOUT", 2.0))

        slip = float(getattr(settings, "AUTOTRADE_SLIPPAGE_PCT", 0.0))

        positions = _get_open_positions(state)
        pending = _get_pending_orders(state)

        # runtime で禁止された side の pending は掃除
        for ticker, od in list((pending or {}).items()):
            side = str((od or {}).get("side") or "").upper().strip()
            if side in ["LONG", "SHORT"] and not _runtime_allows_side(state, side):
                pending.pop(str(ticker), None)

        if _is_force_close_time(now):
            closed = 0
            for ticker, p in list((positions or {}).items()):
                try:
                    bars = _fetch_last_two_bars(ticker=str(ticker))
                    if bars is None:
                        continue
                    _, cur = bars
                    last_price = float(cur["close"])
                    exit_at = cur["dt"]

                    side = str(p.get("side") or "LONG").upper()
                    size = _safe_int(p.get("size"), 0)
                    entry_price = _safe_float(p.get("entry_price"), 0.0)

                    entry_at_s = str(p.get("entry_at") or "")
                    try:
                        entry_at = _ensure_aware(datetime.fromisoformat(entry_at_s))
                    except Exception:
                        entry_at = exit_at

                    if side == "LONG":
                        exit_price = float(last_price) * (1 - slip)
                        pnl = (exit_price - entry_price) * size
                        rr_real = (exit_price - entry_price) / max(entry_price * stop_pct, 1e-9)
                    else:
                        exit_price = float(last_price) * (1 + slip)
                        pnl = (entry_price - exit_price) * size
                        rr_real = (entry_price - exit_price) / max(entry_price * stop_pct, 1e-9)

                    _db_retry(lambda: AutoTradeExecution.objects.create(
                        user=user,
                        snapshot=active,
                        run_detail=None,
                        mode="PAPER",
                        strategy="BREAKOUT",
                        ticker=str(ticker),
                        side=side,
                        entry_at=entry_at,
                        entry_price=float(entry_price),
                        size=int(size),
                        exit_at=exit_at,
                        exit_price=float(exit_price),
                        exit_reason="FORCE",
                        pnl_yen=int(round(pnl)),
                        rr=float(rr_real),
                        holding_minutes=int(max(0, int((exit_at - entry_at).total_seconds() // 60))),
                    ))

                    positions.pop(str(ticker), None)
                    pending.pop(str(ticker), None)
                    closed += 1
                except Exception:
                    continue

            _set_open_positions(state, positions)
            _set_pending_orders(state, pending)
            if closed > 0:
                _append_mode_log(state, mode="PAPER", msg=f"15:25 強制全決済：{closed}件をFORCEでクローズしました。")
            state.updated_at = timezone.now()
            _db_retry(lambda: state.save(update_fields=["rules", "updated_at"]))
            return {"ok": True, "skipped": False, "force_closed": closed}

        rep = str((picks or [""])[0])
        rep_bars = _fetch_last_two_bars(ticker=rep) if rep else None
        if rep_bars is None:
            state.updated_at = timezone.now()
            _db_retry(lambda: state.save(update_fields=["rules", "updated_at"]))
            return {"ok": True, "skipped": True, "reason": "no_bar_data"}

        _, rep_cur = rep_bars
        cur_bar_ts = str(rep_cur["ts"])
        last_bar_ts = _get_last_bar_ts(state)

        if last_bar_ts == cur_bar_ts:
            state.updated_at = timezone.now()
            _db_retry(lambda: state.save(update_fields=["rules", "updated_at"]))
            return {"ok": True, "skipped": True, "reason": "same_bar"}

        _set_last_bar_ts(state, cur_bar_ts)

        filled = 0
        if gate_level != "STOP":
            for ticker, od in list((pending or {}).items()):
                try:
                    if str(ticker) in (positions or {}):
                        pending.pop(str(ticker), None)
                        continue

                    if len(positions or {}) >= int(max_pos):
                        break

                    trades_today = _today_trade_count(user, today)
                    if trades_today >= int(max_trades):
                        break

                    bars = _fetch_last_two_bars(ticker=str(ticker))
                    if bars is None:
                        continue
                    _, cur = bars

                    side = str(od.get("side") or "").upper()
                    if side not in ["LONG", "SHORT"]:
                        pending.pop(str(ticker), None)
                        continue

                    if not _runtime_allows_side(state, side):
                        pending.pop(str(ticker), None)
                        continue

                    raw_open = float(cur["open"])
                    entry_at = cur["dt"]

                    if not _trend_allows(snapshot=active, ticker=str(ticker), entry_dt=entry_at, side=side):
                        pending.pop(str(ticker), None)
                        continue

                    equity_yen = int(state.equity_yen or getattr(settings, "AUTOTRADE_BASE_EQUITY_YEN", 1_000_000))
                    size = _calc_shares(equity_yen=equity_yen, price=float(raw_open), stop_pct=float(stop_pct))
                    if size < 100:
                        pending.pop(str(ticker), None)
                        continue

                    if side == "LONG":
                        entry_price = float(raw_open) * (1 + slip)
                        stop_price = float(entry_price) * (1 - float(stop_pct))
                        take_price = float(entry_price) * (1 + float(stop_pct) * float(rr))
                    else:
                        entry_price = float(raw_open) * (1 - slip)
                        stop_price = float(entry_price) * (1 + float(stop_pct))
                        take_price = float(entry_price) * (1 - float(stop_pct) * float(rr))

                    hold_minutes = int(max(5, int(max_hold_bars) * 5))
                    expire_at = entry_at + timedelta(minutes=hold_minutes)

                    positions[str(ticker)] = {
                        "ticker": str(ticker),
                        "side": side,
                        "size": int(size),
                        "entry_at": timezone.localtime(entry_at).isoformat(),
                        "entry_price": float(entry_price),
                        "stop_price": float(stop_price),
                        "take_price": float(take_price),
                        "expire_at": timezone.localtime(expire_at).isoformat(),
                        "params": {
                            "stop_pct": float(stop_pct),
                            "rr": float(rr),
                            "lookback_bars": int(lookback_bars),
                            "max_hold_bars": int(max_hold_bars),
                            "runtime_hold_cap": (int(hold_cap) if hold_cap is not None else None),
                        },
                        "filled_bar_ts": str(cur["ts"]),
                    }

                    pending.pop(str(ticker), None)
                    filled += 1
                    _append_mode_log(state, mode="PAPER", msg=f"約定（次足OPEN）：{ticker} {side} size={size} entry={entry_price:.3f}")

                except Exception:
                    continue

        _set_open_positions(state, positions)
        _set_pending_orders(state, pending)

        closed = 0
        for ticker, p in list((positions or {}).items()):
            try:
                bars = _fetch_last_two_bars(ticker=str(ticker))
                if bars is None:
                    continue
                _, cur = bars

                side = str(p.get("side") or "LONG").upper()
                size = _safe_int(p.get("size"), 0)
                entry_price = _safe_float(p.get("entry_price"), 0.0)
                stop_price = _safe_float(p.get("stop_price"), 0.0)
                take_price = _safe_float(p.get("take_price"), 0.0)

                entry_at_s = str(p.get("entry_at") or "")
                try:
                    entry_at = _ensure_aware(datetime.fromisoformat(entry_at_s))
                except Exception:
                    entry_at = cur["dt"]

                expire_at_s = str(p.get("expire_at") or "")
                expire_at = None
                try:
                    expire_at = _ensure_aware(datetime.fromisoformat(expire_at_s))
                except Exception:
                    expire_at = None

                high = float(cur["high"])
                low = float(cur["low"])
                exit_at = cur["dt"]

                exit_reason = None
                exit_price = None

                if side == "LONG":
                    if low <= stop_price:
                        exit_reason = "SL"
                        exit_price = float(stop_price) * (1 - slip)
                    elif high >= take_price:
                        exit_reason = "TP"
                        exit_price = float(take_price) * (1 - slip)
                else:
                    if high >= stop_price:
                        exit_reason = "SL"
                        exit_price = float(stop_price) * (1 + slip)
                    elif low <= take_price:
                        exit_reason = "TP"
                        exit_price = float(take_price) * (1 + slip)

                if exit_reason is None and expire_at is not None and exit_at >= expire_at:
                    exit_reason = "TIME"
                    last_price = float(cur["close"])
                    if side == "LONG":
                        exit_price = float(last_price) * (1 - slip)
                    else:
                        exit_price = float(last_price) * (1 + slip)

                if exit_reason is None:
                    continue

                if side == "LONG":
                    pnl = (float(exit_price) - entry_price) * size
                    rr_real = (float(exit_price) - entry_price) / max(entry_price * stop_pct, 1e-9)
                else:
                    pnl = (entry_price - float(exit_price)) * size
                    rr_real = (entry_price - float(exit_price)) / max(entry_price * stop_pct, 1e-9)

                _db_retry(lambda: AutoTradeExecution.objects.create(
                    user=user,
                    snapshot=active,
                    run_detail=None,
                    mode="PAPER",
                    strategy="BREAKOUT",
                    ticker=str(ticker),
                    side=side,
                    entry_at=entry_at,
                    entry_price=float(entry_price),
                    size=int(size),
                    exit_at=exit_at,
                    exit_price=float(exit_price),
                    exit_reason=str(exit_reason),
                    pnl_yen=int(round(pnl)),
                    rr=float(rr_real),
                    holding_minutes=int(max(0, int((exit_at - entry_at).total_seconds() // 60))),
                ))

                positions.pop(str(ticker), None)
                closed += 1

            except Exception:
                continue

        if closed > 0:
            _append_mode_log(state, mode="PAPER", msg=f"クローズ：{closed}件（TP/SL/TIME）")
            _set_open_positions(state, positions)

        if gate_level == "STOP":
            _set_pending_orders(state, pending)
            state.updated_at = timezone.now()
            _db_retry(lambda: state.save(update_fields=["rules", "updated_at"]))
            return {"ok": True, "skipped": True, "reason": "gate_stop", "filled": filled, "closed": closed}

        if _forbid_new_entries_by_time(now):
            if not _allow_hold_past_end():
                pass
            _set_pending_orders(state, pending)
            state.updated_at = timezone.now()
            _db_retry(lambda: state.save(update_fields=["rules", "updated_at"]))
            return {"ok": True, "skipped": True, "reason": "time_forbid_entries", "filled": filled, "closed": closed}

        rules = _get_rules_dict(state)
        ig = rules.get("intraday_guard") if isinstance(rules.get("intraday_guard"), dict) else {}
        ig_res = ig.get("result") if isinstance(ig.get("result"), dict) else {}
        if bool(ig_res.get("forbid_new_entries")):
            _set_pending_orders(state, pending)
            state.updated_at = timezone.now()
            _db_retry(lambda: state.save(update_fields=["rules", "updated_at"]))
            return {"ok": True, "skipped": True, "reason": "intraday_guard_forbid", "filled": filled, "closed": closed}

        trades_today = _today_trade_count(user, today)
        if trades_today >= int(max_trades):
            _append_mode_log(state, mode="PAPER", msg=f"新規禁止：当日取引回数が上限（{trades_today}/{max_trades}）")
            _set_pending_orders(state, pending)
            state.updated_at = timezone.now()
            _db_retry(lambda: state.save(update_fields=["rules", "updated_at"]))
            return {"ok": True, "skipped": True, "reason": "max_trades", "filled": filled, "closed": closed}

        created_orders = 0
        for ticker in (picks or [])[:10]:
            t = str(ticker)

            if t in (positions or {}):
                continue
            if t in (pending or {}):
                continue
            if len(positions or {}) + len(pending or {}) >= int(max_pos):
                break

            sig = _compute_breakout_signal_from_prev_bar(ticker=t, lookback_bars=int(lookback_bars))
            if sig is None:
                continue

            if not _runtime_allows_side(state, str(sig).upper()):
                continue

            pending[t] = {
                "ticker": t,
                "side": str(sig).upper(),
                "created_at": timezone.localtime(timezone.now()).isoformat(),
                "based_on_bar_ts": str(cur_bar_ts),
            }
            created_orders += 1
            _append_mode_log(state, mode="PAPER", msg=f"注文作成（次足OPEN）：{t} {str(sig).upper()}")

            if trades_today + created_orders >= int(max_trades):
                break

        _set_pending_orders(state, pending)
        _set_open_positions(state, positions)

        state.updated_at = timezone.now()
        _db_retry(lambda: state.save(update_fields=["rules", "updated_at"]))

        return {
            "ok": True,
            "skipped": False,
            "bar_ts": cur_bar_ts,
            "filled": filled,
            "closed": closed,
            "orders_created": created_orders,
            "open_positions": len(positions or {}),
            "pending_orders": len(pending or {}),
        }