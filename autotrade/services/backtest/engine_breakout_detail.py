# =========================================================
# [FILE] autotrade/services/backtest/engine_breakout_detail.py
# [PATH] <project_root>/autotrade/services/backtest/engine_breakout_detail.py
#
# このファイルは何？
# - 戦略①（レンジブレイク）の「詳細バックテスト」エンジンです。
# - 1トレード=1件の辞書ログとして返します（DB保存は runner 側の役割）。
#
# 今回の変更（ハイブリッドA：実験室をダッシュボード寄りに統一）：
# - 日足SMAフィルタ（TREND_ONLY）で「SMAが出てない日は見送り」をやめる
#   -> 本番(dashboard/engine_breakout.py) と同じ挙動に統一
#   -> SMA不足日は “制限なし（許可）” 扱いにする
# =========================================================

from __future__ import annotations

from typing import Any, Dict, List

from django.conf import settings
from django.utils import timezone

from .data_fetcher import fetch_5m, fetch_1d


def _ensure_aware(dt):
    if dt is None:
        return None
    if timezone.is_aware(dt):
        return dt
    return timezone.make_aware(dt, timezone.get_current_timezone())


def _build_daily_trend_map(*, ticker: str, sma_days: int) -> Dict[str, str]:
    """
    日足の終値とSMAから、日付 -> "UP"/"DOWN" の辞書を作る。
    keyは "YYYY-MM-DD"。
    """
    sma_days = int(sma_days) if int(sma_days) > 0 else 20

    df = fetch_1d(ticker, prefer_period="1y")
    if df is None or getattr(df, "empty", True):
        return {}

    # SMA計算
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


def run_breakout_detail(
    *,
    snapshot_dict: Dict[str, Any],
    ticker: str,
    window_days: int,
    rr: float,
    base_equity_yen: int = 1_000_000,
) -> Dict[str, Any]:
    """
    戻り値:
      {
        "trades": [ {execution_dict}, ... ],
        "pnls": [int, ...],
        "equity_curve": [float, ...],
        "start_at": datetime,
        "end_at": datetime,
      }
    """

    # 主要パラメータ
    stop_pct = float(snapshot_dict.get("breakout_stop_pct") or 0.003)  # 0.3%
    slip = float(getattr(settings, "AUTOTRADE_SLIPPAGE_PCT", 0.0))
    risk_trade_pct = float(getattr(settings, "AUTOTRADE_RISK_TRADE_PCT", 0.0015))

    # ★ ブレイク判定本数（5分足）
    lookback_bars = int(snapshot_dict.get("breakout_lookback_bars") or 6)
    if lookback_bars < 2:
        lookback_bars = 2
    if lookback_bars > 60:
        lookback_bars = 60

    # ★ 最大保有（bars）
    max_hold_bars = int(snapshot_dict.get("breakout_max_hold_bars") or snapshot_dict.get("max_hold_bars") or 20)
    if max_hold_bars < 1:
        max_hold_bars = 1
    if max_hold_bars > 200:
        max_hold_bars = 200

    # ★ 日足フィルタ
    daily_filter = str(snapshot_dict.get("breakout_daily_filter") or "OFF").upper()
    sma_days = int(snapshot_dict.get("breakout_sma_days") or 20)
    direction = str(snapshot_dict.get("breakout_direction") or "TREND_ONLY").upper()

    if daily_filter not in ["OFF", "SMA"]:
        daily_filter = "OFF"
    if sma_days <= 0:
        sma_days = 20
    if direction not in ["TREND_ONLY", "BOTH"]:
        direction = "TREND_ONLY"

    trend_map: Dict[str, str] = {}
    if daily_filter == "SMA":
        trend_map = _build_daily_trend_map(ticker=ticker, sma_days=sma_days)

    prefer_period = "60d" if window_days > 20 else "20d"
    df = fetch_5m(ticker, prefer_period=prefer_period)
    if df is None or getattr(df, "empty", True):
        return {"trades": [], "pnls": [], "equity_curve": [float(base_equity_yen)], "start_at": None, "end_at": None}

    bars_per_day = 70
    df = df.iloc[-window_days * bars_per_day :].copy()
    if len(df) < 200:
        return {"trades": [], "pnls": [], "equity_curve": [float(base_equity_yen)], "start_at": None, "end_at": None}

    idx = df.index
    start_at = _ensure_aware(idx[0].to_pydatetime() if hasattr(idx[0], "to_pydatetime") else idx[0])
    end_at = _ensure_aware(idx[-1].to_pydatetime() if hasattr(idx[-1], "to_pydatetime") else idx[-1])

    opens = df["open"].values
    highs = df["high"].values
    lows = df["low"].values
    closes = df["close"].values

    equity = float(base_equity_yen)
    equity_curve = [equity]
    pnls: List[int] = []
    trades_out: List[Dict[str, Any]] = []

    def _allowed_side(entry_dt_aware, side: str) -> bool:
        """
        日足SMAフィルタ（TREND_ONLY）を適用。
        - OFFなら常にOK
        - SMA + TREND_ONLY：UP→LONGのみ / DOWN→SHORTのみ
        - SMA + BOTH：両方向OK（trend_mapは作るが制限しない）
        - ★ハイブリッドA：SMAが出てない日は “見送り” ではなく “制限なし(許可)”
          -> 本番(engine_breakout.py) と同じ挙動に統一
        """
        if daily_filter != "SMA":
            return True
        if direction == "BOTH":
            return True

        if not entry_dt_aware:
            return True

        d = timezone.localtime(entry_dt_aware).date().isoformat()
        tr = trend_map.get(d)
        if tr not in ["UP", "DOWN"]:
            # ★SMA不足日は許可（制限なし）
            return True

        if tr == "UP" and side == "LONG":
            return True
        if tr == "DOWN" and side == "SHORT":
            return True
        return False

    # i: 現在バー、エントリーは次バーopen
    start_i = max(lookback_bars + 1, 10)

    for i in range(start_i, len(df) - 2):
        hh = float(max(highs[i - lookback_bars : i]))
        ll = float(min(lows[i - lookback_bars : i]))

        price = float(closes[i])
        nxt_open = float(opens[i + 1])

        risk_yen = float(equity) * risk_trade_pct
        stop_yen_per_share = price * stop_pct
        shares = int((risk_yen / max(stop_yen_per_share, 1e-9)) // 100) * 100
        if shares < 100:
            continue

        entry_at = _ensure_aware(idx[i + 1].to_pydatetime() if hasattr(idx[i + 1], "to_pydatetime") else idx[i + 1])

        # ロング
        if price > hh:
            if not _allowed_side(entry_at, "LONG"):
                continue

            entry = nxt_open * (1.0 + slip)
            stop = entry * (1.0 - stop_pct)
            take = entry * (1.0 + stop_pct * rr)

            exited = False
            j_end = min(i + 1 + max_hold_bars, len(df) - 1)

            for j in range(i + 1, j_end + 1):
                h = float(highs[j])
                l = float(lows[j])

                if l <= stop:
                    exitp = stop * (1.0 - slip)
                    exit_reason = "SL"
                    exited = True
                elif h >= take:
                    exitp = take * (1.0 - slip)
                    exit_reason = "TP"
                    exited = True
                else:
                    continue

                exit_at = _ensure_aware(idx[j].to_pydatetime() if hasattr(idx[j], "to_pydatetime") else idx[j])
                pnl = int(round((exitp - entry) * shares))
                equity += float(pnl)
                equity_curve.append(equity)
                pnls.append(pnl)

                hold_min = int(round((exit_at - entry_at).total_seconds() / 60.0))
                rr_real = (exitp - entry) / max(entry * stop_pct, 1e-9)

                trades_out.append({
                    "ticker": ticker,
                    "side": "LONG",
                    "entry_at": entry_at,
                    "entry_price": float(entry),
                    "exit_at": exit_at,
                    "exit_price": float(exitp),
                    "exit_reason": exit_reason,
                    "size": int(shares),
                    "pnl_yen": int(pnl),
                    "rr": float(rr_real),
                    "holding_minutes": int(hold_min),
                })
                break

            if not exited:
                exit_at = _ensure_aware(idx[j_end].to_pydatetime() if hasattr(idx[j_end], "to_pydatetime") else idx[j_end])
                exitp = float(closes[j_end]) * (1.0 - slip)
                pnl = int(round((exitp - entry) * shares))
                equity += float(pnl)
                equity_curve.append(equity)
                pnls.append(pnl)

                hold_min = int(round((exit_at - entry_at).total_seconds() / 60.0))
                rr_real = (exitp - entry) / max(entry * stop_pct, 1e-9)

                trades_out.append({
                    "ticker": ticker,
                    "side": "LONG",
                    "entry_at": entry_at,
                    "entry_price": float(entry),
                    "exit_at": exit_at,
                    "exit_price": float(exitp),
                    "exit_reason": "TIME",
                    "size": int(shares),
                    "pnl_yen": int(pnl),
                    "rr": float(rr_real),
                    "holding_minutes": int(hold_min),
                })

        # ショート
        elif price < ll:
            if not _allowed_side(entry_at, "SHORT"):
                continue

            entry = nxt_open * (1.0 - slip)
            stop = entry * (1.0 + stop_pct)
            take = entry * (1.0 - stop_pct * rr)

            exited = False
            j_end = min(i + 1 + max_hold_bars, len(df) - 1)

            for j in range(i + 1, j_end + 1):
                h = float(highs[j])
                l = float(lows[j])

                if h >= stop:
                    exitp = stop * (1.0 + slip)
                    exit_reason = "SL"
                    exited = True
                elif l <= take:
                    exitp = take * (1.0 + slip)
                    exit_reason = "TP"
                    exited = True
                else:
                    continue

                exit_at = _ensure_aware(idx[j].to_pydatetime() if hasattr(idx[j], "to_pydatetime") else idx[j])
                pnl = int(round((entry - exitp) * shares))
                equity += float(pnl)
                equity_curve.append(equity)
                pnls.append(pnl)

                hold_min = int(round((exit_at - entry_at).total_seconds() / 60.0))
                rr_real = (entry - exitp) / max(entry * stop_pct, 1e-9)

                trades_out.append({
                    "ticker": ticker,
                    "side": "SHORT",
                    "entry_at": entry_at,
                    "entry_price": float(entry),
                    "exit_at": exit_at,
                    "exit_price": float(exitp),
                    "exit_reason": exit_reason,
                    "size": int(shares),
                    "pnl_yen": int(pnl),
                    "rr": float(rr_real),
                    "holding_minutes": int(hold_min),
                })
                break

            if not exited:
                exit_at = _ensure_aware(idx[j_end].to_pydatetime() if hasattr(idx[j_end], "to_pydatetime") else idx[j_end])
                exitp = float(closes[j_end]) * (1.0 + slip)
                pnl = int(round((entry - exitp) * shares))
                equity += float(pnl)
                equity_curve.append(equity)
                pnls.append(pnl)

                hold_min = int(round((exit_at - entry_at).total_seconds() / 60.0))
                rr_real = (entry - exitp) / max(entry * stop_pct, 1e-9)

                trades_out.append({
                    "ticker": ticker,
                    "side": "SHORT",
                    "entry_at": entry_at,
                    "entry_price": float(entry),
                    "exit_at": exit_at,
                    "exit_price": float(exitp),
                    "exit_reason": "TIME",
                    "size": int(shares),
                    "pnl_yen": int(pnl),
                    "rr": float(rr_real),
                    "holding_minutes": int(hold_min),
                })

    return {
        "trades": trades_out,
        "pnls": pnls,
        "equity_curve": equity_curve,
        "start_at": start_at,
        "end_at": end_at,
    }