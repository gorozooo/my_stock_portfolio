# -*- coding: utf-8 -*-
"""
ファイル: scripts/daytrade_backtest_multi_simple.py

目的（かんたんテスト / ワンタップ）
- 複数銘柄 × 過去N営業日（20/60/120）で、デイトレ5分足バックテストを回す。
- 戦略ロジックは一切変えない（既存: VWAPPullbackLongStrategy のまま）。
- 0トレの日が出るのは仕様。銘柄数を増やして「回る」ようにする。

実行例:
  PYTHONPATH=. DJANGO_SETTINGS_MODULE=config.settings python scripts/daytrade_backtest_multi_simple.py 20 3023 6946 9501
  PYTHONPATH=. DJANGO_SETTINGS_MODULE=config.settings python scripts/daytrade_backtest_multi_simple.py 60 3023

出力:
- 銘柄別サマリ
- 全体サマリ（勝率/avgR/DD/総トレ/総PnL）
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import date, timedelta
from typing import List, Dict, Tuple

import pandas as pd

from aiapp.services.daytrade.policy_loader import load_policy_yaml
from aiapp.services.daytrade.bars_5m_daytrade import load_daytrade_5m_bars
from aiapp.services.daytrade.bar_adapter_5m import df_to_bars_5m
from aiapp.services.daytrade.backtest_runner import run_backtest_one_day


@dataclass
class Agg:
    days: int = 0
    traded_days: int = 0
    total_trades: int = 0
    total_pnl: int = 0
    sum_r: float = 0.0
    wins: int = 0
    losses: int = 0
    max_dd_yen: int = 0  # 最小値（マイナス）を保持


def _last_n_bdays_jst(n: int, end_d: date | None = None) -> List[date]:
    """
    過去N営業日（簡易：平日のみ）。
    ※JPX祝日は未考慮（まず“回す”ことを優先）。
    """
    if end_d is None:
        end_d = date.today()
    # pandas bdate_range: 月〜金のみ
    ds = pd.bdate_range(end=end_d, periods=n).to_pydatetime()
    return [d.date() for d in ds]


def _update_agg(agg: Agg, day_res) -> None:
    agg.days += 1
    agg.total_pnl += int(day_res.pnl_yen)
    agg.total_trades += int(len(day_res.trades))
    if len(day_res.trades) > 0:
        agg.traded_days += 1

    for tr in day_res.trades:
        r = float(tr.r)
        agg.sum_r += r
        if tr.pnl_yen >= 0:
            agg.wins += 1
        else:
            agg.losses += 1

    # day_res.max_drawdown_yen は（0 or マイナス）想定
    try:
        agg.max_dd_yen = min(int(agg.max_dd_yen), int(day_res.max_drawdown_yen))
    except Exception:
        pass


def _fmt_pct(x: float) -> str:
    return f"{x*100:.1f}%"


def run_for_ticker(ticker: str, dates: List[date], policy: dict) -> Agg:
    agg = Agg(max_dd_yen=0)
    for d in dates:
        df = load_daytrade_5m_bars(ticker, d, force_refresh=False)
        if df is None or df.empty:
            # データ無し日はスキップ（0日扱いにしない方が現実的）
            continue

        bars = df_to_bars_5m(df)
        if not bars:
            continue

        res = run_backtest_one_day(bars=bars, policy=policy)
        _update_agg(agg, res)

    return agg


def main():
    if len(sys.argv) < 3:
        print("usage: python scripts/daytrade_backtest_multi_simple.py <20|60|120> <ticker1> [ticker2 ...]")
        sys.exit(1)

    n = int(sys.argv[1])
    tickers = [str(x).strip() for x in sys.argv[2:] if str(x).strip()]
    if n not in (20, 60, 120):
        print("N must be one of 20/60/120")
        sys.exit(1)
    if not tickers:
        print("tickers is empty")
        sys.exit(1)

    policy = load_policy_yaml().policy
    dates = _last_n_bdays_jst(n)

    print("=== daytrade backtest multi (simple) ===")
    print("policy_id =", policy.get("meta", {}).get("policy_id"))
    print("days (bday approx) =", n)
    print("tickers =", tickers)
    print("")

    total = Agg(max_dd_yen=0)

    # 銘柄別
    for t in tickers:
        agg = run_for_ticker(t, dates, policy)

        trades = agg.total_trades
        avg_r = (agg.sum_r / trades) if trades > 0 else 0.0
        winrate = (agg.wins / trades) if trades > 0 else 0.0

        print(f"[{t}] used_days={agg.days} traded_days={agg.traded_days} trades={trades} pnl={agg.total_pnl} "
              f"winrate={_fmt_pct(winrate)} avg_r={avg_r:.4f} max_dd_yen={agg.max_dd_yen}")

        # 全体へ加算
        total.days += agg.days
        total.traded_days += agg.traded_days
        total.total_trades += agg.total_trades
        total.total_pnl += agg.total_pnl
        total.sum_r += agg.sum_r
        total.wins += agg.wins
        total.losses += agg.losses
        total.max_dd_yen = min(total.max_dd_yen, agg.max_dd_yen)

    # 全体
    trades = total.total_trades
    avg_r = (total.sum_r / trades) if trades > 0 else 0.0
    winrate = (total.wins / trades) if trades > 0 else 0.0

    print("")
    print("---- total ----")
    print(f"used_days={total.days} traded_days={total.traded_days} trades={trades} pnl={total.total_pnl} "
          f"winrate={_fmt_pct(winrate)} avg_r={avg_r:.4f} max_dd_yen={total.max_dd_yen}")
    print("=== done ===")


if __name__ == "__main__":
    main()