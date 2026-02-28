# =========================================================
# [FILE] autotrade/services/backtest/engine_breakout.py
# [PATH] <project_root>/autotrade/services/backtest/engine_breakout.py
#
# このファイルは何？
# - 戦略①（レンジブレイク）のバックテストを回すエンジンです。
# - 詳細モードでは Execution（事実ログ）をDBに作成します。
#
# 今回の変更（攻め型の実運用安定化）：
# - 日足SMAフィルタで「SMAが出てない日は見送り」をやめる
#   -> データ不足で trades が激減して gate が不必要にSTOPになるのを防ぐ
#   -> SMA不足日は “制限なし（許可）” 扱いにする
# =========================================================

from __future__ import annotations

from typing import Optional, Any, Dict, List
from django.conf import settings
from django.utils import timezone

from .data_fetcher import fetch_5m, fetch_1d
from .metrics import max_drawdown, profit_factor

from autotrade.models import AutoTradeSettingSnapshot
from autotrade.models_backtest import AutoTradeExecution


def _safe_float(x: Any, default: float) -> float:
    try:
        return float(x)
    except Exception:
        return float(default)


def _safe_int(x: Any, default: int) -> int:
    try:
        return int(x)
    except Exception:
        return int(default)


def _get_nested(d: Dict[str, Any], *keys, default=None):
    cur = d
    for k in keys:
        if not isinstance(cur, dict):
            return default
        if k in cur:
            cur = cur.get(k)
            continue
        ks = str(k)
        if ks in cur:
            cur = cur.get(ks)
            continue
        return default
    return cur if cur is not None else default


def _ensure_aware(dt):
    if dt is None:
        return None
    try:
        if timezone.is_aware(dt):
            return dt
        return timezone.make_aware(dt, timezone.get_current_timezone())
    except Exception:
        return dt


def _build_daily_trend_map(*, ticker: str, sma_days: int) -> Dict[str, str]:
    """
    日足の終値とSMAから、日付 -> "UP"/"DOWN" の辞書を作る。
    keyは "YYYY-MM-DD"。
    """
    sma_days = int(sma_days) if int(sma_days) > 0 else 20

    df = fetch_1d(ticker, prefer_period="1y")
    if df is None or getattr(df, "empty", True):
        return {}

    try:
        close = df["close"]
        sma = close.rolling(window=sma_days).mean()
    except Exception:
        return {}

    out: Dict[str, str] = {}
    idx = df.index

    for i in range(len(df)):
        try:
            c = float(close.iloc[i])
            s = float(sma.iloc[i])
        except Exception:
            continue
        if s != s:  # NaN
            continue

        dt = idx[i]
        d = None
        try:
            d = dt.date().isoformat()
        except Exception:
            try:
                d = str(dt)[:10]
            except Exception:
                d = None

        if not d:
            continue

        out[d] = "UP" if c >= s else "DOWN"

    return out


def _get_stop_pct_breakout_from_snapshot(snapshot: Optional[AutoTradeSettingSnapshot]) -> float:
    """
    Snapshot（固定設定）から BREAKOUT stop_pct を拾う。
    """
    default = float(getattr(settings, "AUTOTRADE_BREAKOUT_STOP_PCT", 0.003))  # 0.30%
    if snapshot is None:
        return default

    sdict = snapshot.snapshot if isinstance(snapshot.snapshot, dict) else {}

    candidates = [
        sdict.get("stop_pct_breakout"),
        sdict.get("breakout_stop_pct"),
        _get_nested(sdict, "breakout", "stop_pct"),
        _get_nested(sdict, "params", "breakout", "stop_pct"),
        _get_nested(sdict, "BREAKOUT", "stop_pct"),
    ]

    for x in candidates:
        if x is None:
            continue
        v = _safe_float(x, default)
        if 0.0 < v < 0.10:
            return float(v)

    return default


def _get_lookback_bars_from_snapshot(snapshot: Optional[AutoTradeSettingSnapshot]) -> int:
    """
    Snapshotからブレイク判定本数（5分足）を拾う。
    """
    default = int(getattr(settings, "AUTOTRADE_BREAKOUT_LOOKBACK_BARS", 6))
    if snapshot is None:
        return default

    sdict = snapshot.snapshot if isinstance(snapshot.snapshot, dict) else {}

    candidates = [
        sdict.get("breakout_lookback_bars"),
        _get_nested(sdict, "breakout", "lookback_bars"),
        _get_nested(sdict, "params", "breakout", "lookback_bars"),
        _get_nested(sdict, "BREAKOUT", "lookback_bars"),
    ]

    v = None
    for x in candidates:
        if x is None:
            continue
        v = _safe_int(x, None)
        if v is None:
            continue
        break

    if v is None:
        v = default

    if v < 2:
        v = 2
    if v > 60:
        v = 60
    return int(v)


def _get_max_hold_bars_from_snapshot(snapshot: Optional[AutoTradeSettingSnapshot]) -> int:
    """
    Snapshotから最大保有バー数を拾う。
    """
    default = int(getattr(settings, "AUTOTRADE_BREAKOUT_MAX_HOLD_BARS", 20))
    if snapshot is None:
        return default

    sdict = snapshot.snapshot if isinstance(snapshot.snapshot, dict) else {}

    candidates_bars = [
        sdict.get("breakout_max_hold_bars"),
        sdict.get("max_hold_bars"),
        _get_nested(sdict, "breakout", "max_hold_bars"),
        _get_nested(sdict, "params", "breakout", "max_hold_bars"),
        _get_nested(sdict, "BREAKOUT", "max_hold_bars"),
    ]

    for x in candidates_bars:
        if x is None:
            continue
        v = _safe_int(x, None)
        if v is None:
            continue
        if v < 1:
            v = 1
        if v > 200:
            v = 200
        return int(v)

    candidates_min = [
        sdict.get("breakout_max_hold_min"),
        _get_nested(sdict, "breakout", "max_hold_min"),
        _get_nested(sdict, "params", "breakout", "max_hold_min"),
        _get_nested(sdict, "BREAKOUT", "max_hold_min"),
    ]

    for x in candidates_min:
        if x is None:
            continue
        m = _safe_int(x, None)
        if m is None:
            continue
        bars = max(1, int(round(float(m) / 5.0)))
        if bars > 200:
            bars = 200
        return int(bars)

    return default


def _get_daily_filter_params_from_snapshot(snapshot: Optional[AutoTradeSettingSnapshot]) -> Dict[str, Any]:
    """
    Snapshotから日足フィルタ設定を拾う（互換込み）。
    """
    out = {"daily_filter": "OFF", "sma_days": 20, "direction": "TREND_ONLY"}
    if snapshot is None:
        return out

    sdict = snapshot.snapshot if isinstance(snapshot.snapshot, dict) else {}

    daily_filter = (
        sdict.get("breakout_daily_filter")
        or _get_nested(sdict, "breakout", "daily_filter")
        or _get_nested(sdict, "params", "breakout", "daily_filter")
        or _get_nested(sdict, "BREAKOUT", "daily_filter")
        or "OFF"
    )
    sma_days = (
        sdict.get("breakout_sma_days")
        or _get_nested(sdict, "breakout", "sma_days")
        or _get_nested(sdict, "params", "breakout", "sma_days")
        or _get_nested(sdict, "BREAKOUT", "sma_days")
        or 20
    )
    direction = (
        sdict.get("breakout_direction")
        or _get_nested(sdict, "breakout", "direction")
        or _get_nested(sdict, "params", "breakout", "direction")
        or _get_nested(sdict, "BREAKOUT", "direction")
        or "TREND_ONLY"
    )

    daily_filter = str(daily_filter or "OFF").upper()
    direction = str(direction or "TREND_ONLY").upper()
    sma_days = _safe_int(sma_days, 20)

    if daily_filter not in ["OFF", "SMA"]:
        daily_filter = "OFF"
    if sma_days <= 0:
        sma_days = 20
    if direction not in ["TREND_ONLY", "BOTH"]:
        direction = "TREND_ONLY"

    out["daily_filter"] = daily_filter
    out["sma_days"] = int(sma_days)
    out["direction"] = direction
    return out


def run_breakout(
    ticker: str,
    window_days: int,
    rr: float,
    *,
    snapshot: Optional[AutoTradeSettingSnapshot] = None,
    mode: Optional[str] = None,
    run_meta=None,
    target_date=None,
):
    """
    互換性のために2モードを持つ：
    1) 旧：簡易集計モード（aggregate用）
    2) 新：詳細バックテストモード（Execution生成）
    """
    slip = float(getattr(settings, "AUTOTRADE_SLIPPAGE_PCT", 0.0))

    df = fetch_5m(ticker, prefer_period="60d" if window_days > 20 else "20d")
    if df is None or getattr(df, "empty", True):
        return None

    bars_per_day = 70
    df = df.iloc[-window_days * bars_per_day :].copy()
    if len(df) < 200:
        return None

    detailed = (snapshot is not None) and (mode is not None)

    # --- snapshot反映（詳細モードのみ） ---
    if detailed:
        stop_pct = _get_stop_pct_breakout_from_snapshot(snapshot)
        lookback_bars = _get_lookback_bars_from_snapshot(snapshot)
        max_hold_bars = _get_max_hold_bars_from_snapshot(snapshot)

        dfp = _get_daily_filter_params_from_snapshot(snapshot)
        daily_filter = str(dfp.get("daily_filter") or "OFF").upper()
        sma_days = int(dfp.get("sma_days") or 20)
        direction = str(dfp.get("direction") or "TREND_ONLY").upper()

        trend_map: Dict[str, str] = {}
        if daily_filter == "SMA":
            trend_map = _build_daily_trend_map(ticker=ticker, sma_days=sma_days)

        def _allowed_side(entry_dt_aware, side: str) -> bool:
            """
            日足SMAフィルタ（TREND_ONLY）を適用。
            - OFFなら常にOK
            - SMA + TREND_ONLY：UP→LONGのみ / DOWN→SHORTのみ
            - SMA + BOTH：両方向OK
            - ★攻め型：SMAが出てない日は “見送り” ではなく “制限なし(許可)” にする
              -> trades激減→gate STOP の事故を避ける
            """
            if daily_filter != "SMA":
                return True
            if direction == "BOTH":
                return True
            if not entry_dt_aware:
                return True

            try:
                d = timezone.localtime(entry_dt_aware).date().isoformat()
            except Exception:
                try:
                    d = str(entry_dt_aware)[:10]
                except Exception:
                    return True

            tr = trend_map.get(d)
            if tr not in ["UP", "DOWN"]:
                # ★SMA不足日は許可（制限なし）
                return True

            if tr == "UP" and side == "LONG":
                return True
            if tr == "DOWN" and side == "SHORT":
                return True
            return False

    else:
        stop_pct = float(getattr(settings, "AUTOTRADE_BREAKOUT_STOP_PCT", 0.003))
        lookback_bars = int(getattr(settings, "AUTOTRADE_BREAKOUT_LOOKBACK_BARS", 6))
        max_hold_bars = int(getattr(settings, "AUTOTRADE_BREAKOUT_MAX_HOLD_BARS", 20))

        def _allowed_side(entry_dt_aware, side: str) -> bool:
            return True

    equity = 1_000_000.0
    equity_curve = [equity]
    pnls: List[float] = []
    trades = 0

    if detailed:
        user = snapshot.user
        base_equity = float(
            (snapshot.snapshot or {}).get(
                "base_equity_yen",
                getattr(settings, "AUTOTRADE_BASE_EQUITY_YEN", 1_000_000),
            )
        )
        equity = base_equity
        equity_curve = [equity]

        if target_date is None:
            target_date = timezone.localdate()

    opens = df["open"].values
    highs = df["high"].values
    lows = df["low"].values
    closes = df["close"].values
    idx = df.index

    i = max(lookback_bars + 1, 10)

    while i < len(df) - 2:
        hh = max(highs[i - lookback_bars : i])
        ll = min(lows[i - lookback_bars : i])

        price = float(closes[i])
        nxt_open = float(opens[i + 1])

        risk_yen = float(equity) * float(getattr(settings, "AUTOTRADE_RISK_TRADE_PCT", 0.0015))
        stop_yen_per_share = price * stop_pct
        shares = int((risk_yen / max(stop_yen_per_share, 1e-9)) // 100) * 100
        if shares < 100:
            i += 1
            continue

        entry_at = _ensure_aware(idx[i + 1].to_pydatetime() if hasattr(idx[i + 1], "to_pydatetime") else idx[i + 1])

        # ロング
        if price > hh:
            if not _allowed_side(entry_at, "LONG"):
                i += 1
                continue

            entry = nxt_open * (1 + slip)
            stop = entry * (1 - stop_pct)
            take = entry * (1 + stop_pct * rr)

            exited = False

            j_end = min(i + 1 + max_hold_bars, len(df) - 1)
            for j in range(i + 1, j_end + 1):
                h = float(highs[j])
                l = float(lows[j])

                if l <= stop:
                    exitp = stop * (1 - slip)
                    pnl = (exitp - entry) * shares
                    equity += pnl
                    pnls.append(pnl)
                    trades += 1
                    equity_curve.append(equity)

                    if detailed:
                        exit_at = _ensure_aware(idx[j].to_pydatetime() if hasattr(idx[j], "to_pydatetime") else idx[j])
                        holding_minutes = int((exit_at - entry_at).total_seconds() // 60)
                        risk_per_share = entry * stop_pct
                        rr_real = (exitp - entry) / max(risk_per_share, 1e-9)

                        AutoTradeExecution.objects.create(
                            user=user,
                            snapshot=snapshot,
                            run_detail=run_meta,
                            mode=str(mode),
                            strategy="BREAKOUT",
                            ticker=ticker,
                            side="LONG",
                            entry_at=entry_at,
                            entry_price=float(entry),
                            size=int(shares),
                            exit_at=exit_at,
                            exit_price=float(exitp),
                            exit_reason="SL",
                            pnl_yen=int(round(pnl)),
                            rr=float(rr_real),
                            holding_minutes=int(max(0, holding_minutes)),
                        )

                    i = j + 1
                    exited = True
                    break

                if h >= take:
                    exitp = take * (1 - slip)
                    pnl = (exitp - entry) * shares
                    equity += pnl
                    pnls.append(pnl)
                    trades += 1
                    equity_curve.append(equity)

                    if detailed:
                        exit_at = _ensure_aware(idx[j].to_pydatetime() if hasattr(idx[j], "to_pydatetime") else idx[j])
                        holding_minutes = int((exit_at - entry_at).total_seconds() // 60)
                        risk_per_share = entry * stop_pct
                        rr_real = (exitp - entry) / max(risk_per_share, 1e-9)

                        AutoTradeExecution.objects.create(
                            user=user,
                            snapshot=snapshot,
                            run_detail=run_meta,
                            mode=str(mode),
                            strategy="BREAKOUT",
                            ticker=ticker,
                            side="LONG",
                            entry_at=entry_at,
                            entry_price=float(entry),
                            size=int(shares),
                            exit_at=exit_at,
                            exit_price=float(exitp),
                            exit_reason="TP",
                            pnl_yen=int(round(pnl)),
                            rr=float(rr_real),
                            holding_minutes=int(max(0, holding_minutes)),
                        )

                    i = j + 1
                    exited = True
                    break

            if not exited:
                exit_at = _ensure_aware(idx[j_end].to_pydatetime() if hasattr(idx[j_end], "to_pydatetime") else idx[j_end])
                exitp = float(closes[j_end]) * (1 - slip)
                pnl = (exitp - entry) * shares
                equity += pnl
                pnls.append(pnl)
                trades += 1
                equity_curve.append(equity)

                if detailed:
                    holding_minutes = int((exit_at - entry_at).total_seconds() // 60)
                    risk_per_share = entry * stop_pct
                    rr_real = (exitp - entry) / max(risk_per_share, 1e-9)

                    AutoTradeExecution.objects.create(
                        user=user,
                        snapshot=snapshot,
                        run_detail=run_meta,
                        mode=str(mode),
                        strategy="BREAKOUT",
                        ticker=ticker,
                        side="LONG",
                        entry_at=entry_at,
                        entry_price=float(entry),
                        size=int(shares),
                        exit_at=exit_at,
                        exit_price=float(exitp),
                        exit_reason="TIME",
                        pnl_yen=int(round(pnl)),
                        rr=float(rr_real),
                        holding_minutes=int(max(0, holding_minutes)),
                    )

                i = j_end + 1
                continue

        # ショート
        elif price < ll:
            if not _allowed_side(entry_at, "SHORT"):
                i += 1
                continue

            entry = nxt_open * (1 - slip)
            stop = entry * (1 + stop_pct)
            take = entry * (1 - stop_pct * rr)

            exited = False

            j_end = min(i + 1 + max_hold_bars, len(df) - 1)
            for j in range(i + 1, j_end + 1):
                h = float(highs[j])
                l = float(lows[j])

                if h >= stop:
                    exitp = stop * (1 + slip)
                    pnl = (entry - exitp) * shares
                    equity += pnl
                    pnls.append(pnl)
                    trades += 1
                    equity_curve.append(equity)

                    if detailed:
                        exit_at = _ensure_aware(idx[j].to_pydatetime() if hasattr(idx[j], "to_pydatetime") else idx[j])
                        holding_minutes = int((exit_at - entry_at).total_seconds() // 60)
                        risk_per_share = entry * stop_pct
                        rr_real = (entry - exitp) / max(risk_per_share, 1e-9)

                        AutoTradeExecution.objects.create(
                            user=user,
                            snapshot=snapshot,
                            run_detail=run_meta,
                            mode=str(mode),
                            strategy="BREAKOUT",
                            ticker=ticker,
                            side="SHORT",
                            entry_at=entry_at,
                            entry_price=float(entry),
                            size=int(shares),
                            exit_at=exit_at,
                            exit_price=float(exitp),
                            exit_reason="SL",
                            pnl_yen=int(round(pnl)),
                            rr=float(rr_real),
                            holding_minutes=int(max(0, holding_minutes)),
                        )

                    i = j + 1
                    exited = True
                    break

                if l <= take:
                    exitp = take * (1 + slip)
                    pnl = (entry - exitp) * shares
                    equity += pnl
                    pnls.append(pnl)
                    trades += 1
                    equity_curve.append(equity)

                    if detailed:
                        exit_at = _ensure_aware(idx[j].to_pydatetime() if hasattr(idx[j], "to_pydatetime") else idx[j])
                        holding_minutes = int((exit_at - entry_at).total_seconds() // 60)
                        risk_per_share = entry * stop_pct
                        rr_real = (entry - exitp) / max(risk_per_share, 1e-9)

                        AutoTradeExecution.objects.create(
                            user=user,
                            snapshot=snapshot,
                            run_detail=run_meta,
                            mode=str(mode),
                            strategy="BREAKOUT",
                            ticker=ticker,
                            side="SHORT",
                            entry_at=entry_at,
                            entry_price=float(entry),
                            size=int(shares),
                            exit_at=exit_at,
                            exit_price=float(exitp),
                            exit_reason="TP",
                            pnl_yen=int(round(pnl)),
                            rr=float(rr_real),
                            holding_minutes=int(max(0, holding_minutes)),
                        )

                    i = j + 1
                    exited = True
                    break

            if not exited:
                exit_at = _ensure_aware(idx[j_end].to_pydatetime() if hasattr(idx[j_end], "to_pydatetime") else idx[j_end])
                exitp = float(closes[j_end]) * (1 + slip)
                pnl = (entry - exitp) * shares
                equity += pnl
                pnls.append(pnl)
                trades += 1
                equity_curve.append(equity)

                if detailed:
                    holding_minutes = int((exit_at - entry_at).total_seconds() // 60)
                    risk_per_share = entry * stop_pct
                    rr_real = (entry - exitp) / max(risk_per_share, 1e-9)

                    AutoTradeExecution.objects.create(
                        user=user,
                        snapshot=snapshot,
                        run_detail=run_meta,
                        mode=str(mode),
                        strategy="BREAKOUT",
                        ticker=ticker,
                        side="SHORT",
                        entry_at=entry_at,
                        entry_price=float(entry),
                        size=int(shares),
                        exit_at=exit_at,
                        exit_price=float(exitp),
                        exit_reason="TIME",
                        pnl_yen=int(round(pnl)),
                        rr=float(rr_real),
                        holding_minutes=int(max(0, holding_minutes)),
                    )

                i = j_end + 1
                continue

        i += 1

    if detailed:
        return {"executions": int(trades)}

    if trades == 0:
        return {"trades": 0, "pf": 0.0, "max_dd": 1.0, "pnl": 0.0}

    return {
        "trades": int(trades),
        "pf": float(profit_factor(pnls)),
        "max_dd": float(max_drawdown(equity_curve)),
        "pnl": float(sum(pnls)),
    }