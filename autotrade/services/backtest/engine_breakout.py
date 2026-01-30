"""
[FILE] autotrade/services/backtest/engine_breakout.py
[PATH] <project_root>/autotrade/services/backtest/engine_breakout.py

このファイルは何？
- 戦略①（レンジブレイク）のバックテストを回すエンジンです。

初心者ポイント：
- “ブレイクしたら入る→損切りor利確” を機械的に再現します。
"""

from django.conf import settings
from .data_fetcher import fetch_5m
from .metrics import max_drawdown, profit_factor


def run_breakout(ticker: str, window_days: int, rr: float):
    stop_pct = 0.003  # 0.3%（初期値：現実寄り）
    slip = settings.AUTOTRADE_SLIPPAGE_PCT

    df = fetch_5m(ticker, prefer_period="60d" if window_days > 20 else "20d")
    if df.empty:
        return None

    # 5分足をざっくり日数分に切る（9:00〜14:30想定で 1日70本程度）
    bars_per_day = 70
    df = df.iloc[-window_days * bars_per_day:].copy()
    if len(df) < 200:
        return None

    equity = 1_000_000.0
    equity_curve = [equity]
    pnls = []
    trades = 0

    opens = df["open"].values
    highs = df["high"].values
    lows = df["low"].values
    closes = df["close"].values

    for i in range(10, len(df) - 1):
        hh = max(highs[i-6:i])  # 直近30分の高値
        ll = min(lows[i-6:i])   # 直近30分の安値

        price = closes[i]
        nxt_open = opens[i+1]

        # ロット計算（100株単位）
        risk_yen = equity * settings.AUTOTRADE_RISK_TRADE_PCT
        stop_yen_per_share = price * stop_pct
        shares = int((risk_yen / max(stop_yen_per_share, 1e-9)) // 100) * 100
        if shares < 100:
            continue

        # ロング
        if price > hh:
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

        # ショート
        elif price < ll:
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