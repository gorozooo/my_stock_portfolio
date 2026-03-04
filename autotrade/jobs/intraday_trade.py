"""
[FILE] autotrade/jobs/intraday_trade.py
[PATH] <project_root>/autotrade/jobs/intraday_trade.py

このファイルは何？
- 場中（毎分）に動く「デモトレード（PAPER）執行ジョブ」です。
- ACTIVE Snapshot（唯一の真実）＋ 今日のDailyState（現場）を使って、
  “実時間の足更新” に合わせて以下を行います：

リアル寄せ（今回の実装）：
1) シグナルは「確定した前の足」で判定（lookahead防止）
2) エントリーは「次の足のOPEN」で約定（リアルの動き）
3) openポジは state.rules["paper_open_positions"] で管理
4) pending注文は state.rules["paper_pending_orders"] で管理
5) 同じ足で何度も処理しないために state.rules["paper_last_bar_ts"] を使う
6) 15:00以降は新規注文禁止、ただし保有は許可（設定スイッチ）
7) 15:25で強制全決済（FORCE）

重要：
- これは本番発注ではない（Executionを作るだけ：PAPER）
- DBに残るのは「クローズしたExecution（事実ログ）」のみ
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple
from datetime import date, datetime, time as dt_time, timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from autotrade.models import AutoTradeDailyState, AutoTradeSettingSnapshot
from autotrade.models_backtest import AutoTradeExecution
from autotrade.services.common.guards import is_emergency_stopped
from autotrade.services.backtest.data_fetcher import fetch_5m

# engine_breakout の “snapshot互換ローダ” を再利用（同じキー解釈にする）
from autotrade.services.backtest.engine_breakout import (
    _get_stop_pct_breakout_from_snapshot,
    _get_lookback_bars_from_snapshot,
    _get_max_hold_bars_from_snapshot,
    _get_daily_filter_params_from_snapshot,
    _build_daily_trend_map,
    _ensure_aware,
)


# =========================================================
# 基本ユーティリティ
# =========================================================
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
    # あなた1人運用前提：ACTIVEを1件だけ取る
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
    """
    日足SMAフィルタ（現場も同じ）
    - OFF：常にOK
    - SMA + BOTH：常にOK
    - SMA + TREND_ONLY：UP→LONGのみ / DOWN→SHORTのみ
    - SMA不足日は許可（攻め型方針）
    """
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


def _append_paper_log(state: AutoTradeDailyState, msg: str) -> None:
    rules = _get_rules_dict(state)
    logs = rules.get("paper_logs")
    if not isinstance(logs, list):
        logs = []
    logs.append({"ts": timezone.localtime(timezone.now()).isoformat(), "msg": str(msg)})
    rules["paper_logs"] = logs[-80:]
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


# =========================================================
# バー（足）の読み取り：リアル寄せの要
# =========================================================
def _fetch_last_two_bars(ticker: str) -> Optional[Tuple[Dict[str, Any], Dict[str, Any]]]:
    """
    5分足を取得し、最後の2本を返す。
    戻り値：
      - prev: ひとつ前（確定済み）扱いの足
      - cur : 最新（今開始した足＝OPENが分かる）扱いの足
    """
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
    """
    シグナルは “確定した前の足” の close を使って判定（lookahead防止）。
    - 直近 lookback_bars 本の高値/安値は “前の足の1本手前まで” で作る。
    - 判定に使う価格は “前の足のclose”。

    戻り値： "LONG" / "SHORT" / None
    """
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

    # prev = -2
    i_prev = len(df) - 2
    i_end = i_prev  # ここまでが “確定” として扱う
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


# =========================================================
# メイン
# =========================================================
@transaction.atomic
def run():
    today = date.today()
    now = _now_jst()

    state, _ = AutoTradeDailyState.objects.get_or_create(date=today)

    # 非常停止は凍結
    if is_emergency_stopped(state):
        return {"ok": True, "skipped": True, "reason": "emergency_stop"}

    active = _get_active_snapshot()
    if active is None:
        _append_paper_log(state, "ACTIVE Snapshot が無いので、PAPERは動かしません。")
        state.updated_at = timezone.now()
        state.save(update_fields=["rules", "updated_at"])
        return {"ok": True, "skipped": True, "reason": "no_active"}

    user = active.user

    picks = _get_today_picks_from_state(state)
    if not picks:
        _append_paper_log(state, "今日のpicksが無い（state.universe）ため、PAPERは見送ります。")
        state.updated_at = timezone.now()
        state.save(update_fields=["rules", "updated_at"])
        return {"ok": True, "skipped": True, "reason": "no_picks"}

    gate_level = str(state.gate_level or "STOP").upper().strip()
    max_pos, max_trades = _gate_limits(gate_level)

    stop_pct = float(_get_stop_pct_breakout_from_snapshot(active))
    lookback_bars = int(_get_lookback_bars_from_snapshot(active))
    max_hold_bars = int(_get_max_hold_bars_from_snapshot(active))

    rr = _safe_float((active.snapshot or {}).get("rr_breakout"), float(getattr(settings, "AUTOTRADE_RR_BREAKOUT", 2.0)))
    if rr <= 0.1:
        rr = float(getattr(settings, "AUTOTRADE_RR_BREAKOUT", 2.0))

    slip = float(getattr(settings, "AUTOTRADE_SLIPPAGE_PCT", 0.0))

    # open/pending
    positions = _get_open_positions(state)
    pending = _get_pending_orders(state)

    # =========================================================
    # 0) 15:25 強制全決済（FORCE）
    # =========================================================
    if _is_force_close_time(now):
        closed = 0
        for ticker, p in list((positions or {}).items()):
            try:
                bars = _fetch_last_two_bars(ticker=str(ticker))
                if bars is None:
                    continue
                prev, cur = bars
                # 強制決済は “最新足のclose” を使う（不利側スリップ）
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

                AutoTradeExecution.objects.create(
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
                )

                positions.pop(str(ticker), None)
                # pendingも消す（もう終わり）
                pending.pop(str(ticker), None)
                closed += 1
            except Exception:
                continue

        _set_open_positions(state, positions)
        _set_pending_orders(state, pending)
        if closed > 0:
            _append_paper_log(state, f"15:25 強制全決済：{closed}件をFORCEでクローズしました。")
        state.updated_at = timezone.now()
        state.save(update_fields=["rules", "updated_at"])
        return {"ok": True, "skipped": False, "force_closed": closed}

    # =========================================================
    # 1) 足更新トリガー（同じ足で二重処理しない）
    #    代表として picks[0] の “最新足 ts” を使う（現場picksが同じ市場なのでズレにくい）
    # =========================================================
    rep = str((picks or [""])[0])
    rep_bars = _fetch_last_two_bars(ticker=rep) if rep else None
    if rep_bars is None:
        state.updated_at = timezone.now()
        state.save(update_fields=["rules", "updated_at"])
        return {"ok": True, "skipped": True, "reason": "no_bar_data"}

    _, rep_cur = rep_bars
    cur_bar_ts = str(rep_cur["ts"])
    last_bar_ts = _get_last_bar_ts(state)

    # まだ足が更新していないなら何もしない（超重要：二重エントリー防止）
    if last_bar_ts == cur_bar_ts:
        state.updated_at = timezone.now()
        state.save(update_fields=["rules", "updated_at"])
        return {"ok": True, "skipped": True, "reason": "same_bar"}

    # この足で処理することを確定
    _set_last_bar_ts(state, cur_bar_ts)

    # =========================================================
    # 2) まず pending を「この足のOPEN」で約定させる（リアル寄せ）
    # =========================================================
    filled = 0
    if gate_level != "STOP":
        for ticker, od in list((pending or {}).items()):
            try:
                # 既にポジ持ちなら約定しない
                if str(ticker) in (positions or {}):
                    pending.pop(str(ticker), None)
                    continue

                # 上限チェック
                if len(positions or {}) >= int(max_pos):
                    break

                trades_today = _today_trade_count(user, today)
                if trades_today >= int(max_trades):
                    break

                bars = _fetch_last_two_bars(ticker=str(ticker))
                if bars is None:
                    continue
                prev, cur = bars

                side = str(od.get("side") or "").upper()
                if side not in ["LONG", "SHORT"]:
                    pending.pop(str(ticker), None)
                    continue

                # “この足のOPEN” で約定（不利側スリップ）
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
                    },
                    "filled_bar_ts": str(cur["ts"]),
                }

                pending.pop(str(ticker), None)
                filled += 1
                _append_paper_log(state, f"約定（次足OPEN）：{ticker} {side} size={size} entry={entry_price:.3f}")

            except Exception:
                continue

    _set_open_positions(state, positions)
    _set_pending_orders(state, pending)

    # =========================================================
    # 3) openポジの決済判定（この足の high/low で TP/SL を再現）
    #    ※エントリーした足で即TP/SLに触れる可能性もここで扱う
    # =========================================================
    closed = 0
    for ticker, p in list((positions or {}).items()):
        try:
            bars = _fetch_last_two_bars(ticker=str(ticker))
            if bars is None:
                continue
            prev, cur = bars

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

            # この足の高値安値で判定
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

            # 時間切れ（max_hold）
            if exit_reason is None and expire_at is not None and exit_at >= expire_at:
                exit_reason = "TIME"
                # 時間切れはこの足のclose（不利側スリップ）
                last_price = float(cur["close"])
                if side == "LONG":
                    exit_price = float(last_price) * (1 - slip)
                else:
                    exit_price = float(last_price) * (1 + slip)

            # 15:00以降の扱い：新規禁止だが保有は許可
            # （ここでは何もしない。15:25でFORCEが走る）

            if exit_reason is None:
                continue

            if side == "LONG":
                pnl = (float(exit_price) - entry_price) * size
                rr_real = (float(exit_price) - entry_price) / max(entry_price * stop_pct, 1e-9)
            else:
                pnl = (entry_price - float(exit_price)) * size
                rr_real = (entry_price - float(exit_price)) / max(entry_price * stop_pct, 1e-9)

            AutoTradeExecution.objects.create(
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
            )

            positions.pop(str(ticker), None)
            closed += 1

        except Exception:
            continue

    if closed > 0:
        _append_paper_log(state, f"クローズ：{closed}件（TP/SL/TIME）")
        _set_open_positions(state, positions)

    # =========================================================
    # 4) 新規シグナル → pending注文を作る（次足OPENで約定）
    # =========================================================
    # gate STOPなら pending を作らない
    if gate_level == "STOP":
        _set_pending_orders(state, pending)
        state.updated_at = timezone.now()
        state.save(update_fields=["rules", "updated_at"])
        return {"ok": True, "skipped": True, "reason": "gate_stop", "filled": filled, "closed": closed}

    # 15:00以降は新規禁止（スイッチ付き）
    if _forbid_new_entries_by_time(now):
        if not _allow_hold_past_end():
            # ここは“持越し禁止モード”にしたい時のフック（今回はONなので基本通らない）
            pass
        _set_pending_orders(state, pending)
        state.updated_at = timezone.now()
        state.save(update_fields=["rules", "updated_at"])
        return {"ok": True, "skipped": True, "reason": "time_forbid_entries", "filled": filled, "closed": closed}

    # intraday_guard が forbid_new_entries を立てているなら pending禁止
    rules = _get_rules_dict(state)
    ig = rules.get("intraday_guard") if isinstance(rules.get("intraday_guard"), dict) else {}
    ig_res = ig.get("result") if isinstance(ig.get("result"), dict) else {}
    if bool(ig_res.get("forbid_new_entries")):
        _set_pending_orders(state, pending)
        state.updated_at = timezone.now()
        state.save(update_fields=["rules", "updated_at"])
        return {"ok": True, "skipped": True, "reason": "intraday_guard_forbid", "filled": filled, "closed": closed}

    # 上限チェック
    trades_today = _today_trade_count(user, today)
    if trades_today >= int(max_trades):
        _append_paper_log(state, f"新規禁止：当日取引回数が上限（{trades_today}/{max_trades}）")
        _set_pending_orders(state, pending)
        state.updated_at = timezone.now()
        state.save(update_fields=["rules", "updated_at"])
        return {"ok": True, "skipped": True, "reason": "max_trades", "filled": filled, "closed": closed}

    # picksを見て、シグナルが出たら pending に置く（約定は次足OPEN）
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

        # 次足OPEN約定のため、ここでは side だけ記録
        pending[t] = {
            "ticker": t,
            "side": str(sig).upper(),
            "created_at": timezone.localtime(timezone.now()).isoformat(),
            "based_on_bar_ts": str(cur_bar_ts),  # この足の更新を見て作った注文
        }
        created_orders += 1
        _append_paper_log(state, f"注文作成（次足OPEN）：{t} {str(sig).upper()}")

        # 取引回数上限（クローズが起きるまで増えないけど、安全側で）
        if trades_today + created_orders >= int(max_trades):
            break

    _set_pending_orders(state, pending)
    _set_open_positions(state, positions)

    state.updated_at = timezone.now()
    state.save(update_fields=["rules", "updated_at"])

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