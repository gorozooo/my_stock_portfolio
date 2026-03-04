"""
[FILE] autotrade/jobs/intraday_trade.py
[PATH] <project_root>/autotrade/jobs/intraday_trade.py

このファイルは何？
- 場中（毎分）に動く「デモトレード（PAPER）執行ジョブ」です。
- ACTIVE Snapshot（唯一の真実）と、今日のDailyState（現場）を使って、
  実時間で “入る/出る” を判断し、AutoTradeExecution（事実ログ）をDBに保存します。

重要な方針：
- これは「本番注文」ではなく、Executionを作るだけ（PAPER）。
- 15:00以降は新規エントリーしない（時間ガード）。
- 15:25で強制全決済（FORCE）。
- openポジは state.rules["paper_open_positions"] に保持して管理する（DBに新テーブルを増やさない）。
"""

from __future__ import annotations

from dataclasses import dataclass
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


def _get_active_snapshot(user) -> Optional[AutoTradeSettingSnapshot]:
    return (
        AutoTradeSettingSnapshot.objects
        .filter(user=user, status="ACTIVE")
        .order_by("-id")
        .first()
    )


def _get_today_picks_from_state(state: AutoTradeDailyState) -> List[str]:
    uni = state.universe if isinstance(state.universe, dict) else {}
    picks = [x.get("ticker") for x in (uni.get("picks") or []) if isinstance(x, dict) and x.get("ticker")]
    picks = [str(x).strip() for x in (picks or []) if str(x).strip()]
    return picks


def _gate_limits(gate_level: str) -> Tuple[int, int]:
    """
    gate_level に応じた当日の上限（ポジ数/取引回数）を返す。
    """
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
    """
    資金管理（確定仕様）：
    - 1トレード最大損失：総資産 × AUTOTRADE_RISK_TRADE_PCT
    - 単位は100株丸め
    """
    risk_pct = float(getattr(settings, "AUTOTRADE_RISK_TRADE_PCT", 0.0015))
    risk_yen = float(equity_yen) * risk_pct
    stop_yen_per_share = float(price) * float(stop_pct)
    if stop_yen_per_share <= 0:
        return 0
    shares = int((risk_yen / stop_yen_per_share) // 100) * 100
    return int(max(0, shares))


def _trend_allows(*, snapshot: AutoTradeSettingSnapshot, ticker: str, entry_dt: datetime, side: str) -> bool:
    """
    日足SMAフィルタを “現場でも同じルール” で適用。
    - OFF：常にOK
    - SMA + BOTH：常にOK
    - SMA + TREND_ONLY：UPならLONGだけ / DOWNならSHORTだけ
    - SMA不足日は「許可」（攻め型の方針を維持）
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


def _get_open_positions_from_state(state: AutoTradeDailyState) -> Dict[str, Any]:
    rules = state.rules if isinstance(state.rules, dict) else {}
    pos = rules.get("paper_open_positions")
    return pos if isinstance(pos, dict) else {}


def _set_open_positions_to_state(state: AutoTradeDailyState, positions: Dict[str, Any]) -> None:
    rules = state.rules if isinstance(state.rules, dict) else {}
    rules["paper_open_positions"] = positions
    state.rules = rules


def _append_paper_log(state: AutoTradeDailyState, msg: str) -> None:
    rules = state.rules if isinstance(state.rules, dict) else {}
    logs = rules.get("paper_logs")
    if not isinstance(logs, list):
        logs = []
    logs.append({
        "ts": timezone.localtime(timezone.now()).isoformat(),
        "msg": str(msg),
    })
    # 50件で丸める
    rules["paper_logs"] = logs[-50:]
    state.rules = rules


def _is_in_session(now: datetime) -> bool:
    start = _parse_hhmm(getattr(settings, "AUTOTRADE_SESSION_START", "09:00"), "09:00")
    end = _parse_hhmm(getattr(settings, "AUTOTRADE_SESSION_END", "15:00"), "15:00")
    t = now.time()
    return (t >= start) and (t <= end)


def _is_force_close_time(now: datetime) -> bool:
    fc = _parse_hhmm(getattr(settings, "AUTOTRADE_FORCE_CLOSE", "15:25"), "15:25")
    return now.time() >= fc


def _forbid_new_entries_by_time(now: datetime) -> bool:
    end = _parse_hhmm(getattr(settings, "AUTOTRADE_SESSION_END", "15:00"), "15:00")
    return now.time() >= end


def _today_trade_count(user, today: date) -> int:
    return (
        AutoTradeExecution.objects
        .filter(user=user, created_at__date=today)
        .exclude(mode="BACKTEST")
        .count()
    )


def _signal_breakout(*, ticker: str, lookback_bars: int) -> Optional[str]:
    """
    直近 lookback_bars 本（5分足）の高値/安値を使って、
    最終確定足の close が
      - 高値ブレイクなら LONG
      - 安値ブレイクなら SHORT
    を返す。

    注意：
    - “次足のopen” は実時間では確定しないので、デモは「最後のclose」を約定価格の土台にする。
    """
    df = fetch_5m(ticker, prefer_period="5d")
    if df is None or getattr(df, "empty", True):
        return None

    if len(df) < (lookback_bars + 5):
        return None

    try:
        highs = df["high"].values
        lows = df["low"].values
        closes = df["close"].values
    except Exception:
        return None

    # 最終行は “直近確定足” として使う（yfinance系の取得でも概ねOK）
    i = len(df) - 1
    lb = int(max(2, min(60, lookback_bars)))

    hh = max(highs[i - lb : i])
    ll = min(lows[i - lb : i])
    price = float(closes[i])

    if price > float(hh):
        return "LONG"
    if price < float(ll):
        return "SHORT"
    return None


def _last_price_and_bar(*, ticker: str) -> Tuple[Optional[float], Optional[datetime], Optional[Dict[str, float]]]:
    """
    最新の5分足から、最後の価格（close）と時刻、そして最後足の high/low を返す。
    """
    df = fetch_5m(ticker, prefer_period="5d")
    if df is None or getattr(df, "empty", True):
        return (None, None, None)
    if len(df) < 5:
        return (None, None, None)

    try:
        row = df.iloc[-1]
        close = float(row["close"])
        high = float(row["high"])
        low = float(row["low"])
        idx = df.index[-1]
        bar_dt = idx.to_pydatetime() if hasattr(idx, "to_pydatetime") else idx
        bar_dt = _ensure_aware(bar_dt)
        return (close, bar_dt, {"high": high, "low": low})
    except Exception:
        return (None, None, None)


@transaction.atomic
def run():
    today = date.today()
    now = _now_jst()

    state, _ = AutoTradeDailyState.objects.get_or_create(date=today)

    # 非常停止は凍結
    if is_emergency_stopped(state):
        return {"ok": True, "skipped": True, "reason": "emergency_stop"}

    # 場外は何もしない（ログだけ残しすぎない）
    if not _is_in_session(now) and not _is_force_close_time(now):
        return {"ok": True, "skipped": True, "reason": "out_of_session"}

    # ACTIVE Snapshot（唯一の真実）
    active = _get_active_snapshot(getattr(state, "user", None))  # stateにはuserが無いので下で拾う
    # stateにuserが無い仕様なので、Execution作成の user は “ACTIVE snapshot.user” から取る
    # ACTIVEが無いとデモは動かせない
    active = _get_active_snapshot(user=None)  # ダミー。下で正しく読み直す

    # ★このプロジェクトは “あなた1人だけ運用” の前提なので、
    # ACTIVE snapshot を1件だけ取る（userを固定で渡せない設計のため）
    active = (
        AutoTradeSettingSnapshot.objects
        .filter(status="ACTIVE")
        .order_by("-id")
        .first()
    )
    if active is None:
        _append_paper_log(state, "ACTIVE Snapshot が無いので、PAPERは動かしません。")
        state.updated_at = timezone.now()
        state.save(update_fields=["rules", "updated_at"])
        return {"ok": True, "skipped": True, "reason": "no_active"}

    user = active.user

    gate_level = str(state.gate_level or "STOP").upper().strip()
    if gate_level == "STOP":
        # STOPの日は、強制決済タイミングだけ処理（持っていれば閉じる）
        pass

    # 今日のpicks（現場）
    picks = _get_today_picks_from_state(state)
    if not picks:
        _append_paper_log(state, "今日のpicksが無い（state.universe）ため、PAPERは見送ります。")
        state.updated_at = timezone.now()
        state.save(update_fields=["rules", "updated_at"])
        return {"ok": True, "skipped": True, "reason": "no_picks"}

    # snapshotからパラメータ取得
    stop_pct = float(_get_stop_pct_breakout_from_snapshot(active))
    lookback_bars = int(_get_lookback_bars_from_snapshot(active))
    max_hold_bars = int(_get_max_hold_bars_from_snapshot(active))
    rr = _safe_float((active.snapshot or {}).get("rr_breakout"), float(getattr(settings, "AUTOTRADE_RR_BREAKOUT", 2.0)))
    if rr <= 0.1:
        rr = float(getattr(settings, "AUTOTRADE_RR_BREAKOUT", 2.0))

    slip = float(getattr(settings, "AUTOTRADE_SLIPPAGE_PCT", 0.0))

    # 上限
    max_pos, max_trades = _gate_limits(gate_level)

    # 既に作った Execution 数（PAPER/LIVEの合計）
    trades_today = _today_trade_count(user, today)

    # open positions
    positions = _get_open_positions_from_state(state)

    # =========================================================
    # 0) FORCE CLOSE（15:25以降）：持っているものを全部閉じる
    # =========================================================
    if _is_force_close_time(now):
        closed = 0
        for key, p in list((positions or {}).items()):
            try:
                ticker = str(p.get("ticker") or "")
                side = str(p.get("side") or "LONG").upper()
                size = _safe_int(p.get("size"), 0)
                entry_price = _safe_float(p.get("entry_price"), 0.0)
                entry_at = _ensure_aware(p.get("entry_at_dt"))  # 互換用（無ければ下の文字列から）
                if entry_at is None:
                    entry_at_s = str(p.get("entry_at") or "")
                    try:
                        entry_at = _ensure_aware(datetime.fromisoformat(entry_at_s))
                    except Exception:
                        entry_at = now

                last_price, bar_dt, _ = _last_price_and_bar(ticker=ticker)
                if last_price is None:
                    continue

                # 強制決済価格（不利側スリップ）
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
                    ticker=ticker,
                    side=side,
                    entry_at=entry_at,
                    entry_price=float(entry_price),
                    size=int(size),
                    exit_at=bar_dt or now,
                    exit_price=float(exit_price),
                    exit_reason="FORCE",
                    pnl_yen=int(round(pnl)),
                    rr=float(rr_real),
                    holding_minutes=int(max(0, int(((bar_dt or now) - entry_at).total_seconds() // 60))),
                )

                positions.pop(key, None)
                closed += 1
            except Exception:
                continue

        _set_open_positions_to_state(state, positions)
        if closed > 0:
            _append_paper_log(state, f"15:25以降の強制決済：{closed}件をFORCEでクローズしました。")
        state.updated_at = timezone.now()
        state.save(update_fields=["rules", "updated_at"])
        return {"ok": True, "skipped": False, "force_closed": closed}

    # =========================================================
    # 1) 既存ポジの監視（TP/SL/TIME）
    # =========================================================
    closed = 0
    for key, p in list((positions or {}).items()):
        try:
            ticker = str(p.get("ticker") or "")
            side = str(p.get("side") or "LONG").upper()
            size = _safe_int(p.get("size"), 0)
            entry_price = _safe_float(p.get("entry_price"), 0.0)
            stop_price = _safe_float(p.get("stop_price"), 0.0)
            take_price = _safe_float(p.get("take_price"), 0.0)

            entry_at_s = str(p.get("entry_at") or "")
            try:
                entry_at = _ensure_aware(datetime.fromisoformat(entry_at_s))
            except Exception:
                entry_at = now

            expire_at_s = str(p.get("expire_at") or "")
            expire_at = None
            try:
                expire_at = _ensure_aware(datetime.fromisoformat(expire_at_s))
            except Exception:
                expire_at = None

            last_price, bar_dt, bar = _last_price_and_bar(ticker=ticker)
            if last_price is None or bar is None:
                continue

            high = float(bar.get("high"))
            low = float(bar.get("low"))

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

            if exit_reason is None and expire_at is not None and (bar_dt or now) >= expire_at:
                exit_reason = "TIME"
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

            AutoTradeExecution.objects.create(
                user=user,
                snapshot=active,
                run_detail=None,
                mode="PAPER",
                strategy="BREAKOUT",
                ticker=ticker,
                side=side,
                entry_at=entry_at,
                entry_price=float(entry_price),
                size=int(size),
                exit_at=bar_dt or now,
                exit_price=float(exit_price),
                exit_reason=str(exit_reason),
                pnl_yen=int(round(pnl)),
                rr=float(rr_real),
                holding_minutes=int(max(0, int(((bar_dt or now) - entry_at).total_seconds() // 60))),
            )

            positions.pop(key, None)
            closed += 1

        except Exception:
            continue

    if closed > 0:
        _append_paper_log(state, f"クローズ：{closed}件（TP/SL/TIME）")
        _set_open_positions_to_state(state, positions)

    # =========================================================
    # 2) 新規エントリー（時間/ゲート/上限/ガードを守る）
    # =========================================================
    # gateがSTOPなら新規禁止
    if gate_level == "STOP":
        _set_open_positions_to_state(state, positions)
        state.updated_at = timezone.now()
        state.save(update_fields=["rules", "updated_at"])
        return {"ok": True, "skipped": True, "reason": "gate_stop"}

    # 時間ガード：15:00以降は新規禁止
    if _forbid_new_entries_by_time(now):
        _set_open_positions_to_state(state, positions)
        state.updated_at = timezone.now()
        state.save(update_fields=["rules", "updated_at"])
        return {"ok": True, "skipped": True, "reason": "time_forbid_entries"}

    # intraday_guard が forbid_new_entries を立てているなら新規禁止
    rules = state.rules if isinstance(state.rules, dict) else {}
    ig = rules.get("intraday_guard") if isinstance(rules.get("intraday_guard"), dict) else {}
    ig_res = ig.get("result") if isinstance(ig.get("result"), dict) else {}
    if bool(ig_res.get("forbid_new_entries")):
        _set_open_positions_to_state(state, positions)
        state.updated_at = timezone.now()
        state.save(update_fields=["rules", "updated_at"])
        return {"ok": True, "skipped": True, "reason": "intraday_guard_forbid"}

    # 上限チェック（取引回数）
    trades_today = _today_trade_count(user, today)
    if trades_today >= int(max_trades):
        _append_paper_log(state, f"新規禁止：当日取引回数が上限（{trades_today}/{max_trades}）")
        _set_open_positions_to_state(state, positions)
        state.updated_at = timezone.now()
        state.save(update_fields=["rules", "updated_at"])
        return {"ok": True, "skipped": True, "reason": "max_trades"}

    # 上限チェック（同時ポジ数）
    open_n = len(positions or {})
    if open_n >= int(max_pos):
        _set_open_positions_to_state(state, positions)
        state.updated_at = timezone.now()
        state.save(update_fields=["rules", "updated_at"])
        return {"ok": True, "skipped": True, "reason": "max_positions"}

    # picksから順に見る（最大10想定）
    entries = 0
    for ticker in (picks or [])[:10]:
        if len(positions or {}) >= int(max_pos):
            break

        # 既に同銘柄を持っているならスキップ
        if str(ticker) in (positions or {}):
            continue

        side = _signal_breakout(ticker=str(ticker), lookback_bars=int(lookback_bars))
        if side is None:
            continue

        # 日足フィルタ
        if not _trend_allows(snapshot=active, ticker=str(ticker), entry_dt=now, side=str(side)):
            continue

        # 最新価格（close）で約定したことにする（PAPER）
        last_price, bar_dt, _ = _last_price_and_bar(ticker=str(ticker))
        if last_price is None:
            continue

        equity_yen = int(state.equity_yen or getattr(settings, "AUTOTRADE_BASE_EQUITY_YEN", 1_000_000))
        size = _calc_shares(equity_yen=equity_yen, price=float(last_price), stop_pct=float(stop_pct))
        if size < 100:
            continue

        entry_at = bar_dt or now

        if str(side).upper() == "LONG":
            entry_price = float(last_price) * (1 + slip)
            stop_price = float(entry_price) * (1 - float(stop_pct))
            take_price = float(entry_price) * (1 + float(stop_pct) * float(rr))
        else:
            entry_price = float(last_price) * (1 - slip)
            stop_price = float(entry_price) * (1 + float(stop_pct))
            take_price = float(entry_price) * (1 - float(stop_pct) * float(rr))

        # max_hold_bars（5分足バー数）→分に直す（5分×bars）
        hold_minutes = int(max(5, int(max_hold_bars) * 5))
        expire_at = entry_at + timedelta(minutes=hold_minutes)

        positions[str(ticker)] = {
            "ticker": str(ticker),
            "side": str(side).upper(),
            "size": int(size),
            "entry_at": timezone.localtime(entry_at).isoformat(),
            "entry_at_dt": entry_at,  # 互換（serializeされても問題ないように上で保険）
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
        }

        entries += 1
        _append_paper_log(state, f"エントリー（PAPER保持）: {ticker} {side} size={size} entry={entry_price:.3f}")

        # 取引回数上限チェック
        trades_today = _today_trade_count(user, today)
        if trades_today + entries >= int(max_trades):
            break

    _set_open_positions_to_state(state, positions)
    state.updated_at = timezone.now()
    state.save(update_fields=["rules", "updated_at"])

    return {"ok": True, "skipped": False, "closed": closed, "entries": entries, "open_positions": len(positions or {})}