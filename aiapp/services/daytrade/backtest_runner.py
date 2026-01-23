# -*- coding: utf-8 -*-
"""
ファイル: aiapp/services/daytrade/backtest_runner.py

これは何？
- デイトレ全自動売買のバックテスト実行エンジン。
- 「次足始値約定」「スリッページ」「確定損益のみ」「デイリミット」を前提に、
  1日分のバー列を時系列に再生してトレード結果を集計する。

重要な仕様
- 約定は次足始値（next_bar.open）を基準にし、スリッページを加える（不利側）
- 損益は「確定のみ」
- デイリミット到達で当日停止
- 終端でポジションが残っていたら強制クローズ（確定損益にする）

フェーズ5で入れるもの
1) slippage_buffer_pct（数量計算の安全バッファ）
   - active.yml の risk.slippage_buffer_pct を参照
   - qty計算に使う「実効リスク予算」を減らし、滑っても実損が上限に収まりやすくする

2) take_profit_r / max_hold_minutes
   - active.yml の exit.take_profit_r / exit.max_hold_minutes を参照
   - 利確（例：1.5R）と時間切れ（例：15分）をバックテストで本番同様に効かせる

3) min_stop_pct / min_stop_yen（stop幅が浅すぎる事故を防ぐ）
   - active.yml の risk.min_stop_pct / risk.min_stop_yen を参照
   - stop幅が浅すぎるシグナルは「見送り」する（補正はしない）
   - early_stop が過敏になる根本原因を、入口で潰す

追加（運用品質）
- Trade に exit_reason を保存し、「何で決済した損益か」を後から分解できるようにする。

追加（A案：運用品質）
- vwap_exit_grace（VWAP割れ即exitの“猶予”）
  - active.yml の exit.vwap_exit_grace を参照
  - strategy_exit（VWAP割れ等）だけを抑制する（stop/take_profit/time_limitは最優先のまま）
  - 例：
    exit:
      vwap_exit_grace:
        enable: true
        min_r_to_allow_exit: 0.3
        grace_minutes_after_entry: 5

追加（B案 改：運用品質）
- time_limit_profit_guard（“勝ちを守る”ガード）をトレーリング型にする
  - 既存ログで guard が 0%勝率になりやすい（戻りの底で投げる）問題を防ぐ
  - 方式：
    - MFE（最大含み益R）が trigger_mfe_r を超えたら「トレーリング開始」
    - exit_line = max(mfe_r - trail_r, keep_r)
      r_now <= exit_line で撤退（※keep_rで“利益が残る”ことを保証）
    - min_hold_minutes で発動を遅らせる
  - 例：
    exit:
      time_limit_profit_guard:
        enable: true
        trigger_mfe_r: 0.25
        trail_r: 0.30
        keep_r: 0.05
        min_hold_minutes: 10

置き場所
- aiapp/services/daytrade/backtest_runner.py
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


def _get_bool(d: Dict[str, Any], key: str, default: bool) -> bool:
    try:
        v = d.get(key, default)
        if isinstance(v, bool):
            return v
        if isinstance(v, (int, float)):
            return bool(v)
        if isinstance(v, str):
            s = v.strip().lower()
            if s in ("1", "true", "yes", "y", "on"):
                return True
            if s in ("0", "false", "no", "n", "off"):
                return False
        return bool(v)
    except Exception:
        return bool(default)


def _make_trade_safe(
    *,
    entry_dt,
    exit_dt,
    entry_price: float,
    exit_price: float,
    qty: int,
    pnl_yen: int,
    r: float,
    exit_reason: str,
    # optional metrics (存在するなら入れる)
    hold_minutes: Optional[float] = None,
    mfe_r: Optional[float] = None,
    mae_r: Optional[float] = None,
) -> Trade:
    """
    Trade の dataclass 定義が環境差分で揺れても落ちないようにする。
    - まず拡張フィールド込みで生成を試し、TypeErrorなら最小構成にフォールバック。
    """
    try:
        return Trade(
            entry_dt=entry_dt,
            exit_dt=exit_dt,
            entry_price=entry_price,
            exit_price=exit_price,
            qty=qty,
            pnl_yen=pnl_yen,
            r=r,
            exit_reason=exit_reason,
            hold_minutes=hold_minutes,
            mfe_r=mfe_r,
            mae_r=mae_r,
        )
    except TypeError:
        # 最小構成
        try:
            return Trade(
                entry_dt=entry_dt,
                exit_dt=exit_dt,
                entry_price=entry_price,
                exit_price=exit_price,
                qty=qty,
                pnl_yen=pnl_yen,
                r=r,
                exit_reason=exit_reason,
            )
        except TypeError:
            # さらに古い定義
            return Trade(
                entry_dt=entry_dt,
                exit_dt=exit_dt,
                entry_price=entry_price,
                exit_price=exit_price,
                qty=qty,
                pnl_yen=pnl_yen,
                r=r,
            )


def run_backtest_one_day(
    bars: List[Bar],
    policy: Dict[str, Any],
    strategy: Optional[BaseStrategy] = None,
) -> DayResult:
    if not bars:
        raise BacktestError("bars is empty.")

    strategy = strategy or VWAPPullbackLongStrategy()

    # --- policy（セクションごとに取り出す）---
    capital_cfg = policy.get("capital", {})
    risk_cfg = policy.get("risk", {})
    tf_cfg = policy.get("time_filter", {})
    strat_cfg = policy.get("strategy", {})
    exit_cfg = policy.get("exit", {})
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
        slippage_buffer_pct = 0.95

    # stop幅の下限（浅すぎるstopを弾く）
    # 例: min_stop_pct=0.001(0.1%), min_stop_yen=2
    min_stop_pct = _get_float(risk_cfg, "min_stop_pct", 0.0)
    min_stop_yen = _get_float(risk_cfg, "min_stop_yen", 0.0)
    if min_stop_pct < 0:
        min_stop_pct = 0.0
    if min_stop_yen < 0:
        min_stop_yen = 0.0

    session_start = _parse_hhmm(tf_cfg["session_start"])
    session_end = _parse_hhmm(tf_cfg["session_end"])
    exclude_ranges = [(_parse_hhmm(a), _parse_hhmm(b)) for a, b in tf_cfg.get("exclude_ranges", [])]

    slippage_pct = float(strat_cfg["slippage_pct"])
    max_trades_per_day = int(limits_cfg["max_trades_per_day"])

    # exitセクションから読む（← active.yml と一致）
    take_profit_r = _get_float(exit_cfg, "take_profit_r", 1.5)
    max_hold_minutes = _get_int(exit_cfg, "max_hold_minutes", 15)

    # --- A案：VWAP割れ即exitの猶予（strategy_exitのみ）---
    vwap_grace_cfg = exit_cfg.get("vwap_exit_grace", {})
    if not isinstance(vwap_grace_cfg, dict):
        vwap_grace_cfg = {}

    vwap_exit_grace_enable = _get_bool(vwap_grace_cfg, "enable", False)
    vwap_exit_grace_min_r = _get_float(vwap_grace_cfg, "min_r_to_allow_exit", 0.0)
    vwap_exit_grace_minutes = _get_int(vwap_grace_cfg, "grace_minutes_after_entry", 0)
    if vwap_exit_grace_min_r < 0:
        vwap_exit_grace_min_r = 0.0
    if vwap_exit_grace_minutes < 0:
        vwap_exit_grace_minutes = 0

    # --- B案 改：勝ちを守る（トレーリング型） ---
    guard_cfg = exit_cfg.get("time_limit_profit_guard", {})
    if not isinstance(guard_cfg, dict):
        guard_cfg = {}

    guard_enable = _get_bool(guard_cfg, "enable", False)
    guard_trigger_mfe_r = _get_float(guard_cfg, "trigger_mfe_r", 0.25)
    guard_trail_r = _get_float(guard_cfg, "trail_r", 0.30)
    guard_keep_r = _get_float(guard_cfg, "keep_r", 0.05)
    guard_min_hold_minutes = _get_int(guard_cfg, "min_hold_minutes", 10)

    if guard_trigger_mfe_r < 0:
        guard_trigger_mfe_r = 0.0
    if guard_trail_r < 0:
        guard_trail_r = 0.0
    if guard_keep_r < 0:
        guard_keep_r = 0.0
    if guard_min_hold_minutes < 0:
        guard_min_hold_minutes = 0

    budget: RiskBudget = calc_risk_budget_yen(base_capital, trade_loss_pct, day_loss_pct)

    # qty計算にだけバッファを効かせる
    effective_trade_loss_yen = int(budget.trade_loss_yen * (1.0 - slippage_buffer_pct))
    effective_trade_loss_yen = max(effective_trade_loss_yen, 1)

    # 利確判定はR定義を壊さないため、基準は trade_loss_yen を使用
    take_profit_yen = float(budget.trade_loss_yen) * float(take_profit_r)

    denom_trade_loss = float(budget.trade_loss_yen) if float(budget.trade_loss_yen) > 0 else 0.0

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

    # intratrade metrics
    mfe_yen = 0.0  # max favorable excursion (yen)
    mae_yen = 0.0  # max adverse excursion (yen; negative)
    max_favorable_price = None
    min_adverse_price = None

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
        if (not has_position) and max_positions < 1:
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

            # Stop価格：VWAP割れ + 0.1%マージン（安全側）
            stop_price = float(bar.vwap) * (1.0 - 0.001)

            # --- ★ stop幅が浅すぎる場合は見送り ---
            # min_stop = max(entry_price * min_stop_pct, min_stop_yen)
            min_stop = max(float(entry_price) * float(min_stop_pct), float(min_stop_yen))
            if min_stop > 0:
                if abs(float(entry_price) - float(stop_price)) < float(min_stop):
                    # リセットして見送り
                    entry_price = 0.0
                    entry_dt = None
                    stop_price = 0.0
                    qty = 0
                    continue

            qty_calc = safe_qty_from_risk_long(
                entry_price=entry_price,
                stop_price=stop_price,
                trade_loss_yen=effective_trade_loss_yen,  # ★バッファ適用
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

            # intratrade metrics reset
            mfe_yen = 0.0
            mae_yen = 0.0
            max_favorable_price = float(entry_price)
            min_adverse_price = float(entry_price)

            continue

        # =========================
        # EXIT（優先順位）
        # 1) ストップ
        # 2) 戦略exit（VWAP割れ等）※A案の猶予はここだけ
        # 3) 利確（take_profit_r）
        # 4) 利益保護ガード（トレーリング型）※“勝ちを守る”だけ
        # 5) 時間切れ（max_hold_minutes）
        # =========================
        if has_position:
            # intratrade update (bar.high/low を使う：closeだけより現実的)
            try:
                if max_favorable_price is None:
                    max_favorable_price = float(entry_price)
                if min_adverse_price is None:
                    min_adverse_price = float(entry_price)

                max_favorable_price = max(float(max_favorable_price), float(bar.high))
                min_adverse_price = min(float(min_adverse_price), float(bar.low))

                mfe_yen = (float(max_favorable_price) - float(entry_price)) * float(qty)
                mae_yen = (float(min_adverse_price) - float(entry_price)) * float(qty)  # negative
            except Exception:
                pass

            unrealized_yen = (float(bar.close) - float(entry_price)) * float(qty)
            r_now = (float(unrealized_yen) / denom_trade_loss) if denom_trade_loss > 0 else 0.0
            mfe_r = (float(mfe_yen) / denom_trade_loss) if denom_trade_loss > 0 else 0.0
            mae_r = (float(mae_yen) / denom_trade_loss) if denom_trade_loss > 0 else 0.0

            held_minutes_now = 0.0
            if entry_dt is not None:
                try:
                    held_minutes_now = (bar.dt - entry_dt).total_seconds() / 60.0
                except Exception:
                    held_minutes_now = 0.0

            hit_stop = float(bar.close) <= float(stop_price)

            # --- 戦略exit（A案：猶予を入れるのはここだけ） ---
            hit_strategy_exit = (sig.action == "exit")
            if hit_strategy_exit and vwap_exit_grace_enable:
                within_grace = False
                if entry_dt is not None and vwap_exit_grace_minutes > 0:
                    within_grace = held_minutes_now < float(vwap_exit_grace_minutes)

                # 条件を満たす間は「戦略exitだけ」抑制（stop/take_profit/time_limitは別）
                if (float(r_now) < float(vwap_exit_grace_min_r)) or within_grace:
                    hit_strategy_exit = False

            hit_take_profit = unrealized_yen >= take_profit_yen

            # --- B案 改：利益保護ガード（トレーリング型） ---
            hit_profit_guard = False
            if guard_enable and (not hit_stop) and (not hit_take_profit) and (not hit_strategy_exit):
                # 一定時間は発動しない（早すぎる“底売り”防止）
                if held_minutes_now >= float(guard_min_hold_minutes):
                    # まず「十分伸びた（MFE達成）」が条件
                    if float(mfe_r) >= float(guard_trigger_mfe_r):
                        # exit_line = max(mfe_r - trail_r, keep_r)
                        exit_line = max(float(mfe_r) - float(guard_trail_r), float(guard_keep_r))
                        # “利益を守る”なので、利益が残るラインを割ったら撤退
                        if float(r_now) <= float(exit_line):
                            hit_profit_guard = True

            hit_time_stop = False
            if entry_dt is not None and max_hold_minutes > 0:
                if held_minutes_now >= float(max_hold_minutes):
                    hit_time_stop = True

            if hit_stop or hit_strategy_exit or hit_take_profit or hit_profit_guard or hit_time_stop:
                # exit_reason（優先順位は仕様どおり）
                if hit_stop:
                    exit_reason = "stop_loss"
                elif hit_strategy_exit:
                    rr = (sig.reason or "").strip()
                    exit_reason = f"strategy_exit({rr})" if rr else "strategy_exit"
                elif hit_take_profit:
                    exit_reason = "take_profit"
                elif hit_profit_guard:
                    exit_reason = "time_limit_guard"  # 既存集計キー互換（guard枠に入る）
                elif hit_time_stop:
                    exit_reason = "time_limit"
                else:
                    exit_reason = "unknown"

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

                tr = _make_trade_safe(
                    entry_dt=entry_dt,
                    exit_dt=exit_dt,
                    entry_price=entry_price,
                    exit_price=exit_price,
                    qty=qty,
                    pnl_yen=pnl,
                    r=r,
                    exit_reason=exit_reason,
                    hold_minutes=float(held_minutes_now),
                    mfe_r=float(mfe_r),
                    mae_r=float(mae_r),
                )
                trades.append(tr)

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

                has_position = False
                entry_price = 0.0
                entry_dt = None
                qty = 0
                stop_price = 0.0

                mfe_yen = 0.0
                mae_yen = 0.0
                max_favorable_price = None
                min_adverse_price = None

                continue

    # =========================
    # 終端 強制クローズ（確定損益にする）
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

        held_minutes_now = 0.0
        try:
            held_minutes_now = (last_bar.dt - entry_dt).total_seconds() / 60.0
        except Exception:
            held_minutes_now = 0.0

        denom = float(budget.trade_loss_yen) if float(budget.trade_loss_yen) > 0 else 0.0
        mfe_r = (float(mfe_yen) / denom) if denom > 0 else 0.0
        mae_r = (float(mae_yen) / denom) if denom > 0 else 0.0

        trades.append(
            _make_trade_safe(
                entry_dt=entry_dt,
                exit_dt=exit_dt,
                entry_price=entry_price,
                exit_price=exit_price,
                qty=qty,
                pnl_yen=pnl,
                r=r,
                exit_reason="force_close_end_of_day",
                hold_minutes=float(held_minutes_now),
                mfe_r=float(mfe_r),
                mae_r=float(mae_r),
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