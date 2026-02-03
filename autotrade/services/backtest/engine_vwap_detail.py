"""
[FILE] autotrade/services/backtest/engine_vwap_detail.py
[PATH] <project_root>/autotrade/services/backtest/engine_vwap_detail.py

このファイルは何？
- 戦略②（VWAP押し目）の「詳細バックテスト」エンジンです。
- 1トレード=1件の辞書ログとして返します（DB保存は runner 側）。

初心者ポイント：
- “VWAPより上は買いだけ / 下は売りだけ” を守って事故を減らします。
"""

from __future__ import annotations

from typing import Any, Dict, List

from django.conf import settings
from django.utils import timezone

from .data_fetcher import fetch_5m


def _ensure_aware(dt):
    if dt is None:
        return None
    if timezone.is_aware(dt):
        return dt
    return timezone.make_aware(dt, timezone.get_current_timezone())


def run_vwap_detail(
    *,
    snapshot_dict: Dict[str, Any],
    ticker: str,
    window_days: int,
    rr: float,
    base_equity_yen: int = 1_000_000,
) -> Dict[str, Any]:
    stop_pct = float(snapshot_dict.get("vwap_stop_pct") or 0.0025)  # 0.25%
    slip = float(getattr(settings, "AUTOTRADE_SLIPPAGE_PCT", 0.0))
    risk_trade_pct = float(getattr(settings, "AUTOTRADE_RISK_TRADE_PCT", 0.0015))

    prefer_period = "60d" if window_days > 20 else "20d"
    df = fetch_5m(ticker, prefer_period=prefer_period)
    if df is None or getattr(df, "empty", True):
        return {"trades": [], "pnls": [], "equity_curve": [float(base_equity_yen)], "start_at": None, "end_at": None}

    bars_per_day = 70
    df = df.iloc[-window_days * bars_per_day :].copy()
    if len(df) < 200:
        return {"trades": [], "pnls": [], "equity_curve": [float(base_equity_yen)], "start_at": None, "end_at": None}

    # 簡易VWAP（既存簡易版踏襲：累積）
    pv = (df["close"] * df["volume"]).cumsum()
    vv = (df["volume"]).cumsum().replace(0, 1)
    df["vwap"] = pv / vv

    idx = df.index
    start_at = _ensure_aware(idx[0].to_pydatetime() if hasattr(idx[0], "to_pydatetime") else idx[0])
    end_at = _ensure_aware(idx[-1].to_pydatetime() if hasattr(idx[-1], "to_pydatetime") else idx[-1])

    opens = df["open"].values
    highs = df["high"].values
    lows = df["low"].values
    closes = df["close"].values
    vwaps = df["vwap"].values

    equity = float(base_equity_yen)
    equity_curve = [equity]
    pnls: List[int] = []
    trades_out: List[Dict[str, Any]] = []

    max_hold_bars = int(snapshot_dict.get("max_hold_bars") or 20)

    for i in range(5, len(df) - 2):
        price = float(closes[i])
        vwap = float(vwaps[i])
        nxt_open = float(opens[i + 1])

        risk_yen = float(equity) * risk_trade_pct
        stop_yen_per_share = price * stop_pct
        shares = int((risk_yen / max(stop_yen_per_share, 1e-9)) // 100) * 100
        if shares < 100:
            continue

        # ---------------- 買いのみ（VWAP上） ----------------
        if price > vwap:
            # 押し目：安値がVWAP近辺に触れた
            if float(lows[i]) <= vwap * 1.0005:
                entry = nxt_open * (1.0 + slip)
                stop = entry * (1.0 - stop_pct)
                take = entry * (1.0 + stop_pct * rr)
                entry_at = _ensure_aware(idx[i + 1].to_pydatetime() if hasattr(idx[i + 1], "to_pydatetime") else idx[i + 1])

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

        # ---------------- 売りのみ（VWAP下） ----------------
        elif price < vwap:
            if float(highs[i]) >= vwap * 0.9995:
                entry = nxt_open * (1.0 - slip)
                stop = entry * (1.0 + stop_pct)
                take = entry * (1.0 - stop_pct * rr)
                entry_at = _ensure_aware(idx[i + 1].to_pydatetime() if hasattr(idx[i + 1], "to_pydatetime") else idx[i + 1])

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