# -*- coding: utf-8 -*-
"""
ファイル: scripts/daytrade_backtest_one_day_min_test.py

目的（最小検証）
- daytrade専用の5分足取得 → Bar変換 → backtest_runner.run_backtest_one_day を実行して
  1日分が最後まで回ることを確認する。

使い方（Django shellで流す）
  python manage.py shell < scripts/daytrade_backtest_one_day_min_test.py
"""

from datetime import date, timedelta

from aiapp.services.daytrade.bars_5m_daytrade import load_daytrade_5m_bars
from aiapp.services.daytrade.bar_adapter_5m import df_to_bars_5m
from aiapp.services.daytrade.policy_loader import load_policy_yaml
from aiapp.services.daytrade.backtest_runner import run_backtest_one_day


def _find_recent_day_with_data(code: str, lookback_days: int = 10):
    """
    直近n日で「データが取れる日」を探す（祝日/週末対策）
    """
    today = date.today()
    for k in range(0, lookback_days):
        d = today - timedelta(days=k)
        df = load_daytrade_5m_bars(code, d, force_refresh=False)
        if df is not None and len(df) >= 5:
            return d
    return None


def main():
    code = "3023"

    d = _find_recent_day_with_data(code, lookback_days=14)
    if d is None:
        print("[NG] no daytrade 5m data found in recent days")
        return

    print("=== daytrade backtest one-day min test ===")
    print("code =", code, "date =", d.isoformat())

    df = load_daytrade_5m_bars(code, d, force_refresh=False)
    print("df rows =", len(df), "cols =", list(df.columns))

    bars = df_to_bars_5m(df)
    print("bars =", len(bars))

    policy = load_policy_yaml().policy

    day = run_backtest_one_day(bars=bars, policy=policy)

    print("---- result ----")
    print("date_str =", day.date_str)
    print("trades =", len(day.trades))
    print("pnl_yen =", day.pnl_yen)
    print("day_limit_hit =", day.day_limit_hit)
    print("max_drawdown_yen =", day.max_drawdown_yen)
    print("max_consecutive_losses =", day.max_consecutive_losses)

    if day.trades:
        t = day.trades[-1]
        print("---- last trade ----")
        print("entry", t.entry_dt, t.entry_price, "exit", t.exit_dt, t.exit_price, "qty", t.qty, "pnl", t.pnl_yen, "R", t.r)

    print("=== done ===")


main()