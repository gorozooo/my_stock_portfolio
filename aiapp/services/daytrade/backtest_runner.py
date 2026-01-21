# -*- coding: utf-8 -*-
"""
ファイル: aiapp/services/daytrade/backtest_runner.py

これは何？
- デイトレ全自動売買のバックテスト実行エンジン。
- 「次足始値約定」「スリッページ」「固定損失（0.3%）」を前提に
  本番と同じ条件で1日を再生する。

今回の重要ポイント（超重要）
- 戦略が enter を返しても、最後まで exit 条件が満たされないことがある。
  例）データの最後の足でVWAP割れが起きる／ループが len(bars)-1 で止まる
- その場合、ポジションを持ったまま終了して trades=0 になる事故が起きる。
-  لذلك、バックテストでは「終端での強制クローズ」を必ず行う。
  → バックテスト結果（確定損益）として確実に集計できる。

置き場所（重要）
- プロジェクトルート（manage.py がある階層）から見て:
  aiapp/services/daytrade/backtest_runner.py
"""

from __future__ import annotations

from datetime import time
from typing import Any, Dict, List, Optional, Tuple

from .execution_sim import Fill, market_fill
from .risk_math import (
    RiskBudget,
    calc_r,
    calc_risk_budget_yen,
    safe_qty_from_risk_long,
)
from .strategies import VWAPPullbackLongStrategy
from .types import Bar, BaseStrategy, DayResult, StrategySignal, Trade


class BacktestError(RuntimeError):
    pass


def _parse_hhmm(s: str) -> time:
    hh, mm = str(s).split(":")
    return time(int(hh), int(mm))


def _in_time_range(t: time, start: time, end: time) -> bool:
    return (t >= start) and (t <= end)


def _in_exclude_ranges(t: time, ranges: List[Tuple[time, time]]) -> bool:
    return any(a <= t <= b for a, b in ranges)


def run_backtest_one_day(
    bars: List[Bar],
    policy: Dict[str, Any],
    strategy: Optional[BaseStrategy] = None,
) -> DayResult:
    if not bars:
        raise BacktestError("bars is empty.")

    strategy = strategy or VWAPPullbackLongStrategy()

    # --- policy ---
    base_capital = int(policy["capital"]["base_capital"])
    trade_loss_pct = float(policy["risk"]["trade_loss_pct"])
    day_loss_pct = float(policy["risk"]["day_loss_pct"])
    max_positions = int(policy["risk"]["max_positions"])

    session_start = _parse_hhmm(policy["time_filter"]["session_start"])
    session_end = _parse_hhmm(policy["time_filter"]["session_end"])

    exclude_ranges = [
        (_parse_hhmm(a), _parse_hhmm(b))
        for a, b in policy["time_filter"].get("exclude_ranges", [])
    ]

    slippage_pct = float(policy["strategy"]["slippage_pct"])
    max_trades_per_day = int(policy["limits"]["max_trades_per_day"])

    budget: RiskBudget = calc_risk_budget_yen(base_capital, trade_loss_pct, day_loss_pct)

    # --- state ---
    trades: List[Trade] = []
    has_position = False

    entry_price = 0.0
    entry_dt = None
    qty = 0
    stop_price = 0.0

    day_pnl = 0
    day_limit_hit = False

    equity = 0
    peak = 0
    max_dd = 0

    consecutive_losses = 0
    max_consecutive_losses = 0

    date_str = bars[0].dt.date().isoformat()

    # ループは「次足始値約定」のため len(bars)-1 まで
    for i in range(len(bars) - 1):
        bar = bars[i]
        next_bar = bars[i + 1]
        t = bar.dt.time()

        if not _in_time_range(t, session_start, session_end):
            continue
        if _in_exclude_ranges(t, exclude_ranges):
            continue
        if day_limit_hit:
            break
        if len(trades) >= max_trades_per_day and not has_position:
            break
        if not has_position and max_positions < 1:
            raise BacktestError("max_positions must be >= 1")

        sig: StrategySignal = strategy.on_bar(i=i, bars=bars, has_position=has_position, policy=policy)

        # --- ENTRY ---
        if (not has_position) and sig.action == "enter":
            fill: Fill = market_fill(
                next_bar_open=float(next_bar.open),
                side="buy",
                slippage_pct=slippage_pct,
            )
            entry_price = float(fill.price)
            entry_dt = next_bar.dt

            # Stop価格：VWAP割れ + 0.1%マージン
            # ※ bar.vwap は「この時点の判断足」のVWAPを使う
            stop_price = float(bar.vwap) * (1.0 - 0.001)

            qty_calc = safe_qty_from_risk_long(
                entry_price=entry_price,
                stop_price=stop_price,
                trade_loss_yen=budget.trade_loss_yen,
            )
            if not qty_calc or qty_calc <= 0:
                # リスク条件を満たせないので見送り
                entry_price = 0.0
                entry_dt = None
                stop_price = 0.0
                qty = 0
                continue

            qty = int(qty_calc)
            has_position = True
            continue

        # --- EXIT ---
        # 1) 戦略の exit
        # 2) stop_price 到達（bar.close <= stop）
        if has_position and (sig.action == "exit" or float(bar.close) <= float(stop_price)):
            fill = market_fill(
                next_bar_open=float(next_bar.open),
                side="sell",
                slippage_pct=slippage_pct,
            )
            exit_price = float(fill.price)
            exit_dt = next_bar.dt

            pnl = int((exit_price - entry_price) * qty)
            day_pnl += pnl
            r = calc_r(pnl, budget.trade_loss_yen)

            trades.append(
                Trade(
                    entry_dt=entry_dt,
                    exit_dt=exit_dt,
                    entry_price=entry_price,
                    exit_price=exit_price,
                    qty=qty,
                    pnl_yen=pnl,
                    r=r,
                )
            )

            # 連敗
            if pnl < 0:
                consecutive_losses += 1
                max_consecutive_losses = max(max_consecutive_losses, consecutive_losses)
            else:
                consecutive_losses = 0

            # ドローダウン（確定損益ベース）
            equity += pnl
            peak = max(peak, equity)
            max_dd = min(max_dd, equity - peak)

            # デイリミット（確定損益のみ）
            if day_pnl <= -budget.day_loss_yen:
                day_limit_hit = True

            # reset position
            has_position = False
            entry_price = 0.0
            entry_dt = None
            qty = 0
            stop_price = 0.0
            continue

    # =========================
    # 終端 強制クローズ（重要）
    # =========================
    # ループが len(bars)-1 で止まるため、
    # データの最後でexit条件が来た場合や、exitが一度も来ない場合に
    # ポジションが残ることがある。
    # バックテスト結果を「確定損益のみ」で出すため、最後に必ずクローズする。
    if has_position and entry_dt is not None and qty > 0:
        last_bar = bars[-1]
        fill = market_fill(
            next_bar_open=float(last_bar.close),  # 終端は close を基準に（現実は成行なので不利側に寄せてOK）
            side="sell",
            slippage_pct=slippage_pct,
        )
        exit_price = float(fill.price)
        exit_dt = last_bar.dt

        pnl = int((exit_price - entry_price) * qty)
        day_pnl += pnl
        r = calc_r(pnl, budget.trade_loss_yen)

        trades.append(
            Trade(
                entry_dt=entry_dt,
                exit_dt=exit_dt,
                entry_price=entry_price,
                exit_price=exit_price,
                qty=qty,
                pnl_yen=pnl,
                r=r,
            )
        )

        # 連敗/DDも更新（終端分）
        if pnl < 0:
            consecutive_losses += 1
            max_consecutive_losses = max(max_consecutive_losses, consecutive_losses)
        else:
            consecutive_losses = 0

        equity += pnl
        peak = max(peak, equity)
        max_dd = min(max_dd, equity - peak)

        if day_pnl <= -budget.day_loss_yen:
            day_limit_hit = True

    return DayResult(
        date_str=date_str,
        trades=trades,
        pnl_yen=day_pnl,
        day_limit_hit=day_limit_hit,
        max_drawdown_yen=max_dd,
        max_consecutive_losses=max_consecutive_losses,
    )