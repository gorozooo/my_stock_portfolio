"""
[FILE] autotrade/services/backtest/engine_vwap.py
[PATH] <project_root>/autotrade/services/backtest/engine_vwap.py

このファイルは何？
- 戦略②（VWAP押し目）のバックテストを回すエンジンです。

初心者ポイント：
- “VWAPより上は買いだけ / 下は売りだけ” を徹底して事故を減らします。
"""

from django.conf import settings
from .data_fetcher import fetch_5m
from .metrics import max_drawdown, profit_factor


def run_vwap(ticker: str, window_days: int, rr: float):
    stop_pct = 0.0025  # 0.25%
    slip = settings.AUTOTRADE_SLIPPAGE_PCT

    df = fetch_5m(ticker, prefer_period="60d" if window_days > 20 else "20d")
    if df.empty:
        return None

    bars_per_day = 70
    df = df.iloc[-window_days * bars_per_day:].copy()
    if len(df) < 200:
        return None

    # 簡易VWAP
    pv = (df["close"] * df["volume"]).cumsum()
    vv = (df["volume"]).cumsum().replace(0, 1)
    df["vwap"] = pv / vv

    equity = 1_000_000.0
    equity_curve = [equity]
    pnls = []
    trades = 0

    opens = df["open"].values
    highs = df["high"].values
    lows = df["low"].values
    closes = df["close"].values
    vwaps = df["vwap"].values

    for i in range(5, len(df) - 2):
        price = closes[i]
        vwap = vwaps[i]
        nxt_open = opens[i+1]

        risk_yen = equity * settings.AUTOTRADE_RISK_TRADE_PCT
        stop_yen_per_share = price * stop_pct
        shares = int((risk_yen / max(stop_yen_per_share, 1e-9)) // 100) * 100
        if shares < 100:
            continue

        # 買いのみ（VWAP上）
        if price > vwap:
            if lows[i] <= vwap * 1.0005:
                entry = nxt_open * (1 + slip)
                stop = entry * (1 - stop_pct)
                take = entry * (1 + stop_pct * rr)

                for j in range(i+1, min(i+1+20, len(df))):
                    h = highs[j]; l = lows[j]
                    if l <= stop:
                        exitp = stop * (1 - slip)
                        pnl = (exitp - entry) * shares
                        equity += pnl; pnls.append(pnl); trades += 1; equity_curve.append(equity)
                        break
                    if h >= take:
                        exitp = take * (1 - slip)
                        pnl = (exitp - entry) * shares
                        equity += pnl; pnls.append(pnl); trades += 1; equity_curve.append(equity)
                        break

        # 売りのみ（VWAP下）
        elif price < vwap:
            if highs[i] >= vwap * 0.9995:
                entry = nxt_open * (1 - slip)
                stop = entry * (1 + stop_pct)
                take = entry * (1 - stop_pct * rr)

                for j in range(i+1, min(i+1+20, len(df))):
                    h = highs[j]; l = lows[j]
                    if h >= stop:
                        exitp = stop * (1 + slip)
                        pnl = (entry - exitp) * shares
                        equity += pnl; pnls.append(pnl); trades += 1; equity_curve.append(equity)
                        break
                    if l <= take:
                        exitp = take * (1 + slip)
                        pnl = (entry - exitp) * shares
                        equity += pnl; pnls.append(pnl); trades += 1; equity_curve.append(equity)
                        break

    if trades == 0:
        return {"trades": 0, "pf": 0.0, "max_dd": 1.0, "pnl": 0.0}

    return {
        "trades": int(trades),
        "pf": float(profit_factor(pnls)),
        "max_dd": float(max_drawdown(equity_curve)),
        "pnl": float(sum(pnls)),
    }