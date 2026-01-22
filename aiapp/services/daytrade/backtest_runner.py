# -*- coding: utf-8 -*-
"""
ファイル: aiapp/services/daytrade/backtest_runner.py

これは何？
- デイトレ全自動売買のバックテスト実行エンジン。
- 「次足始値約定」「スリッページ」「固定リスク（例：3000円/トレード）」を前提に、
  本番と同じ条件で1日を再生します。

重要な仕様（このファイルの挙動）
- 約定は次足始値（next_bar.open）を基準にし、スリッページを加える（不利側）
- 損益は「確定のみ」
- デイリミット到達で当日停止
- ループ終端でポジションが残っていたら強制クローズ（確定損益にする）

フェーズ5で追加したもの
1) slippage_buffer_pct（数量計算の安全バッファ）
   - 例：20%なら、3000円ではなく2400円でqtyを計算する
   - “理論上の最大損失3000円”を「滑り込みで実質3000円以下」に寄せる目的

2) take_profit_r（利確）
   - 例：1.5Rなら、含み益が +1.5 * trade_loss_yen 以上になったら利確

3) max_hold_minutes（時間切れ）
   - 例：15分なら、エントリーから15分経ったら時間切れでクローズ

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


def _get_float(d: Dict[str, Any], key: str, default: float) -> float:
    try:
        v = d.get(key, default)
        if v is None:
            return float(default)
        return float(v)
    except Exception:
        return float(default)


def _get_int(d: Dict[str, Any], key: str, default: int) -> int:
    try:
        v = d.get(key, default)
        if v is None:
            return int(default)
        return int(v)
    except Exception:
        return int(default)


def run_backtest_one_day(
    bars: List[Bar],
    policy: Dict[str, Any],
    strategy: Optional[BaseStrategy] = None,
) -> DayResult:
    if not bars:
        raise BacktestError("bars is empty.")

    strategy = strategy or VWAPPullbackLongStrategy()

    # --- policy ---
    capital_cfg = policy.get("capital", {})
    risk_cfg = policy.get("risk", {})
    tf_cfg = policy.get("time_filter", {})
    strat_cfg = policy.get("strategy", {})
    limits_cfg = policy.get("limits", {})

    base_capital = int(capital_cfg["base_capital"])
    trade_loss_pct = float(risk_cfg["trade_loss_pct"])
    day_loss_pct = float(risk_cfg["day_loss_pct"])
    max_positions = int(risk_cfg["max_positions"])

    # qty計算の安全バッファ（例：0.20 = 20%）
    slippage_buffer_pct = _get_float(risk_cfg, "slippage_buffer_pct", 0.0)
    if slippage_buffer_pct < 0:
        slippage_buffer_pct = 0.0
    if slippage_buffer_pct >= 0.95:
        # さすがにやりすぎ防止（qtyがほぼ0になる）
        slippage_buffer_pct = 0.95

    session_start = _parse_hhmm(tf_cfg["session_start"])
    session_end = _parse_hhmm(tf_cfg["session_end"])

    exclude_ranges = [
        (_parse_hhmm(a), _parse_hhmm(b))
        for a, b in tf_cfg.get("exclude_ranges", [])
    ]

    slippage_pct = float(strat_cfg["slippage_pct"])
    max_trades_per_day = int(limits_cfg["max_trades_per_day"])

    # 利確R / 時間切れ（なければデフォルト）
    take_profit_r = _get_float(strat_cfg, "take_profit_r", 1.5)
    max_hold_minutes = _get_int(strat_cfg, "max_hold_minutes", 15)

    budget: RiskBudget = calc_risk_budget_yen(base_capital, trade_loss_pct, day_loss_pct)

    # qty計算にだけバッファを効かせる（“実損3000円以下”に寄せる）
    effective_trade_loss_yen = int(budget.trade_loss_yen * (1.0 - slippage_buffer_pct))
    effective_trade_loss_yen = max(effective_trade_loss_yen, 1)

    # 利確判定は“Rの定義（基準）”を崩したくないので、基準は trade_loss_yen のまま
    take_profit_yen = float(budget.trade_loss_yen) * float(take_profit_r)

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

        # =========================
        # ENTRY
        # =========================
        if (not has_position) and sig.action == "enter":
            fill: Fill = market_fill(
                next_bar_open=float(next_bar.open),
                side="buy",
                slippage_pct=slippage_pct,
            )
            entry_price = float(fill.price)
            entry_dt = next_bar.dt

            # Stop価格：VWAP割れ + 0.1%マージン
            stop_price = float(bar.vwap) * (1.0 - 0.001)

            qty_calc = safe_qty_from_risk_long(
                entry_price=entry_price,
                stop_price=stop_price,
                trade_loss_yen=effective_trade_loss_yen,  # ★ここがバッファ適用ポイント
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

        # =========================
        # EXIT判定（優先順位）
        # 1) ストップ（安全優先）
        # 2) 戦略exit（VWAP割れ等）
        # 3) 利確（1.5R）
        # 4) 時間切れ（15分）
        # =========================
        if has_position:
            # 含み損益（このバー終値ベースの“判定用”）
            unrealized_yen = (float(bar.close) - float(entry_price)) * float(qty)

            hit_stop = float(bar.close) <= float(stop_price)
            hit_strategy_exit = (sig.action == "exit")
            hit_take_profit = unrealized_yen >= take_profit_yen

            hit_time_stop = False
            if entry_dt is not None and max_hold_minutes > 0:
                held_minutes = (bar.dt - entry_dt).total_seconds() / 60.0
                if held_minutes >= float(max_hold_minutes):
                    hit_time_stop = True

            if hit_stop or hit_strategy_exit or hit_take_profit or hit_time_stop:
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
    if has_position and entry_dt is not None and qty > 0:
        last_bar = bars[-1]
        fill = market_fill(
            next_bar_open=float(last_bar.close),
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