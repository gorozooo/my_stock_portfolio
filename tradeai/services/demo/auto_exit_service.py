# =========================================================
# [FILE] auto_exit_service.py
# [PATH] <project_root>/tradeai/services/demo/auto_exit_service.py
#
# このファイルは何？
# - OPEN中のデモ建玉を自動終了するサービスです。
# - 利確 / 損切 / 最大保有日数で CLOSED にします。
# - 同日TP/SL両到達は、保守的に不利側（損切）を優先します。
#
# 今回の修正：
# - 「まだ翌営業日足がなくて判定できない」と
#   「本当に価格取得に失敗した」を分けて扱います。
# =========================================================

from __future__ import annotations

from datetime import datetime, time, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

import yfinance as yf
from django.db import transaction
from django.utils import timezone

from tradeai.models.demo_trade import DemoTrade


DEFAULT_MAX_HOLD_BARS = 10

LOAD_STATUS_OK = "ok"
LOAD_STATUS_NOT_READY = "not_ready"
LOAD_STATUS_PRICE_ERROR = "price_error"


def _to_symbol(raw_ticker: str) -> str:
    value = (raw_ticker or "").strip().upper()
    if value.endswith(".T"):
        return value
    if value.isdigit():
        return f"{value}.T"
    return value


def _to_decimal(value: Any) -> Decimal:
    return Decimal(str(float(value))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _aware_market_close(day_value) -> datetime:
    naive = datetime.combine(day_value, time(15, 0))
    return timezone.make_aware(naive, timezone.get_current_timezone())


def _load_daily_bars_after_entry(trade: DemoTrade) -> tuple[str, list[dict[str, Any]]]:
    """
    戻り値:
    - ("ok", bars)          : 翌営業日以降の足があり、判定可能
    - ("not_ready", [])     : 銘柄データ自体はあるが、まだ翌営業日足がない
    - ("price_error", [])   : 本当に価格取得失敗 / 解析不能
    """
    symbol = _to_symbol(trade.ticker)
    start_date = trade.entry_at.date() - timedelta(days=7)

    try:
        df = yf.Ticker(symbol).history(
            start=start_date.isoformat(),
            interval="1d",
            auto_adjust=False,
        )
    except Exception:
        return LOAD_STATUS_PRICE_ERROR, []

    if df is None or df.empty:
        return LOAD_STATUS_PRICE_ERROR, []

    rows: list[dict[str, Any]] = []
    parseable_count = 0

    for idx, row in df.iterrows():
        try:
            trade_date = idx.to_pydatetime().date()
            open_price = float(row["Open"])
            high_price = float(row["High"])
            low_price = float(row["Low"])
            close_price = float(row["Close"])
        except Exception:
            continue

        parseable_count += 1

        if trade_date <= trade.entry_at.date():
            continue

        rows.append(
            {
                "date": trade_date,
                "open": open_price,
                "high": high_price,
                "low": low_price,
                "close": close_price,
            }
        )

    if rows:
        return LOAD_STATUS_OK, rows

    if parseable_count > 0:
        return LOAD_STATUS_NOT_READY, []

    return LOAD_STATUS_PRICE_ERROR, []


def _calc_mfe_mae(direction: str, entry_price: float, bars: list[dict[str, Any]]) -> tuple[float, float]:
    max_favorable = 0.0
    max_adverse = 0.0

    for bar in bars:
        high = float(bar["high"])
        low = float(bar["low"])

        if direction == DemoTrade.DirectionChoices.LONG:
            favorable = ((high - entry_price) / entry_price) * 100.0
            adverse = ((low - entry_price) / entry_price) * 100.0
        else:
            favorable = ((entry_price - low) / entry_price) * 100.0
            adverse = ((entry_price - high) / entry_price) * 100.0

        max_favorable = max(max_favorable, favorable)
        max_adverse = min(max_adverse, adverse)

    return round(max_favorable, 2), round(max_adverse, 2)


def _calc_pnl(direction: str, entry_price: float, close_price: float, qty: int) -> tuple[float, float]:
    if direction == DemoTrade.DirectionChoices.LONG:
        pnl_yen = (close_price - entry_price) * qty
    else:
        pnl_yen = (entry_price - close_price) * qty

    base = entry_price * qty
    pnl_pct = (pnl_yen / base * 100.0) if base else 0.0
    return round(pnl_yen, 2), round(pnl_pct, 2)


def _result_label_from_pnl(pnl_yen: float) -> str:
    if pnl_yen > 0:
        return DemoTrade.ResultLabelChoices.WIN
    if pnl_yen < 0:
        return DemoTrade.ResultLabelChoices.LOSS
    return DemoTrade.ResultLabelChoices.FLAT


def _judge_exit_for_trade(
    trade: DemoTrade,
    bars: list[dict[str, Any]],
    max_hold_bars: int,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    direction = trade.direction
    take_profit_price = float(trade.take_profit_price) if trade.take_profit_price is not None else None
    stop_price = float(trade.stop_price) if trade.stop_price is not None else None

    evaluated_bars: list[dict[str, Any]] = []

    for index, bar in enumerate(bars, start=1):
        evaluated_bars.append(bar)

        high = float(bar["high"])
        low = float(bar["low"])
        close = float(bar["close"])
        exit_at = _aware_market_close(bar["date"])

        if direction == DemoTrade.DirectionChoices.LONG:
            hit_stop = stop_price is not None and low <= stop_price
            hit_tp = take_profit_price is not None and high >= take_profit_price

            if hit_stop and hit_tp:
                return (
                    {
                        "close_at": exit_at,
                        "close_price": stop_price,
                        "exit_reason": "同日に利確・損切の両方へ到達したため、保守的に損切で終了",
                    },
                    evaluated_bars,
                )
            if hit_stop:
                return (
                    {
                        "close_at": exit_at,
                        "close_price": stop_price,
                        "exit_reason": "損切価格に到達したため自動終了",
                    },
                    evaluated_bars,
                )
            if hit_tp:
                return (
                    {
                        "close_at": exit_at,
                        "close_price": take_profit_price,
                        "exit_reason": "利確価格に到達したため自動終了",
                    },
                    evaluated_bars,
                )

        else:
            hit_stop = stop_price is not None and high >= stop_price
            hit_tp = take_profit_price is not None and low <= take_profit_price

            if hit_stop and hit_tp:
                return (
                    {
                        "close_at": exit_at,
                        "close_price": stop_price,
                        "exit_reason": "同日に利確・損切の両方へ到達したため、保守的に損切で終了",
                    },
                    evaluated_bars,
                )
            if hit_stop:
                return (
                    {
                        "close_at": exit_at,
                        "close_price": stop_price,
                        "exit_reason": "損切価格に到達したため自動終了",
                    },
                    evaluated_bars,
                )
            if hit_tp:
                return (
                    {
                        "close_at": exit_at,
                        "close_price": take_profit_price,
                        "exit_reason": "利確価格に到達したため自動終了",
                    },
                    evaluated_bars,
                )

        if index >= max_hold_bars:
            return (
                {
                    "close_at": exit_at,
                    "close_price": close,
                    "exit_reason": f"最大保有日数 {max_hold_bars} 本に到達したため時価で終了",
                },
                evaluated_bars,
            )

    return None, evaluated_bars


@transaction.atomic
def auto_close_demo_trades_for_user(user, max_hold_bars: int = DEFAULT_MAX_HOLD_BARS) -> dict[str, Any]:
    open_trades = list(
        DemoTrade.objects.filter(
            user=user,
            status=DemoTrade.StatusChoices.OPEN,
        ).order_by("entry_at", "id")
    )

    closed_trades: list[DemoTrade] = []
    kept_open_count = 0
    not_ready_count = 0
    price_error_count = 0

    not_ready_trades: list[DemoTrade] = []
    price_error_trades: list[DemoTrade] = []

    for trade in open_trades:
        load_status, bars = _load_daily_bars_after_entry(trade)

        if load_status == LOAD_STATUS_NOT_READY:
            not_ready_count += 1
            not_ready_trades.append(trade)
            continue

        if load_status == LOAD_STATUS_PRICE_ERROR:
            price_error_count += 1
            price_error_trades.append(trade)
            continue

        decision, evaluated_bars = _judge_exit_for_trade(
            trade=trade,
            bars=bars,
            max_hold_bars=max_hold_bars,
        )

        if not decision:
            kept_open_count += 1
            continue

        entry_price = float(trade.entry_price)
        close_price = float(decision["close_price"])
        qty = int(trade.qty or 0)

        pnl_yen, pnl_pct = _calc_pnl(
            direction=trade.direction,
            entry_price=entry_price,
            close_price=close_price,
            qty=qty,
        )
        max_favorable_pct, max_adverse_pct = _calc_mfe_mae(
            direction=trade.direction,
            entry_price=entry_price,
            bars=evaluated_bars,
        )
        result_label = _result_label_from_pnl(pnl_yen)

        trade.status = DemoTrade.StatusChoices.CLOSED
        trade.result_label = result_label
        trade.close_at = decision["close_at"]
        trade.close_price = _to_decimal(close_price)
        trade.pnl_yen = _to_decimal(pnl_yen)
        trade.pnl_pct = _to_decimal(pnl_pct)
        trade.max_favorable_pct = _to_decimal(max_favorable_pct)
        trade.max_adverse_pct = _to_decimal(max_adverse_pct)
        trade.exit_reason = str(decision["exit_reason"] or "").strip()
        trade.save(
            update_fields=[
                "status",
                "result_label",
                "close_at",
                "close_price",
                "pnl_yen",
                "pnl_pct",
                "max_favorable_pct",
                "max_adverse_pct",
                "exit_reason",
                "updated_at",
            ]
        )
        closed_trades.append(trade)

    return {
        "open_count_before": len(open_trades),
        "closed_count": len(closed_trades),
        "kept_open_count": kept_open_count,
        "not_ready_count": not_ready_count,
        "price_error_count": price_error_count,
        "no_data_count": not_ready_count + price_error_count,
        "not_ready_trades": not_ready_trades,
        "price_error_trades": price_error_trades,
        "closed_trades": closed_trades,
    }