# -*- coding: utf-8 -*-
"""
ファイル: aiapp/services/daytrade/backtest_runner.py

これは何？
- 1日分のローソク（バー）を上から順に再生して、
  “本番と同じ前提”でトレードした場合の結果を集計する「バックテストの心臓（骨格）」です。

このファイルが担当すること（フェーズ3）
- バー列（1分足など）を時系列に処理
- 取引できる時間帯（time_filter）を尊重
- 最大ポジション数（基本1）を尊重
- デイリミット（1日の最大損失）に到達したら当日停止
- 取引結果（確定損益のみ）を集計して返す

このファイルが“まだ”担当しないこと（次のステップで実装）
- VWAP押し目の細かいエントリー条件
- 銘柄ユニバース選定
- ATRなどの指標計算
- 複数日・複数銘柄の外側ループ（後で追加）

置き場所（重要）
- プロジェクトルート（manage.py がある階層）から見て:
  aiapp/services/daytrade/backtest_runner.py

入力データの形（フェーズ3）
- まずは「すでに用意されたバー列（Barのリスト）」を受け取る形にする。
  → データ取得は別サービスで後から足す（責任分離で壊れにくい）。

用語
- Bar: 1本の足（1分足など）
- Trade: 1回の売買（エントリー〜イグジットの確定損益）
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from typing import Any, Dict, List, Literal, Optional, Tuple

from .execution_sim import Fill, market_fill
from .risk_math import RiskBudget, calc_r, calc_risk_budget_yen


Side = Literal["long"]


class BacktestError(RuntimeError):
    """バックテスト実行中に前提が崩れた場合の例外。"""


@dataclass(frozen=True)
class Bar:
    """
    1本の足（最小限のフィールド）

    dt:
      その足の時刻（datetime）
      ※タイムゾーンはアプリ内で統一して扱う（まずは naive でもOK）

    open/high/low/close:
      価格

    vwap:
      VWAP（将来、計算して付与してもOK。今は入力に含める設計）

    volume:
      出来高
    """
    dt: datetime
    open: float
    high: float
    low: float
    close: float
    vwap: float
    volume: float


@dataclass
class Trade:
    """
    1回のトレード結果（確定損益）

    entry_dt / exit_dt:
      エントリー/イグジット時刻

    entry_price / exit_price:
      約定価格（スリッページ反映済み）

    qty:
      株数

    pnl_yen:
      損益（円）※確定のみ

    r:
      損益をR換算したもの
    """
    entry_dt: datetime
    exit_dt: datetime
    entry_price: float
    exit_price: float
    qty: int
    pnl_yen: int
    r: float


@dataclass
class DayResult:
    """
    1日分の結果（集計用）
    """
    date_str: str
    trades: List[Trade]
    pnl_yen: int
    day_limit_hit: bool
    max_drawdown_yen: int
    max_consecutive_losses: int


def _parse_hhmm(s: str) -> time:
    hh, mm = s.split(":")
    return time(int(hh), int(mm))


def _in_time_range(t: time, start: time, end: time) -> bool:
    return (t >= start) and (t <= end)


def _in_exclude_ranges(t: time, ranges: List[Tuple[time, time]]) -> bool:
    for a, b in ranges:
        if t >= a and t <= b:
            return True
    return False


@dataclass(frozen=True)
class StrategySignal:
    """
    戦略が返す「次のアクション」。

    action:
      "enter" か "exit" か "hold"

    reason:
      デバッグ用（UIの“理由”にも使える）
    """
    action: Literal["enter", "exit", "hold"]
    reason: str = ""


class BaseStrategy:
    """
    戦略インターフェース（フェーズ3では骨格のみ）

    - on_bar() が毎バー呼ばれる
    - エントリー/イグジットの指示を返す
    """

    def on_bar(self, i: int, bars: List[Bar], has_position: bool, policy: Dict[str, Any]) -> StrategySignal:
        return StrategySignal(action="hold", reason="base strategy (no-op)")


def run_backtest_one_day(
    bars: List[Bar],
    policy: Dict[str, Any],
    strategy: Optional[BaseStrategy] = None,
) -> DayResult:
    """
    1日分のバー列を再生してバックテストする（確定損益のみ）。

    注意:
    - フェーズ3では「戦略は差し替え可能」にして、まずエンジンを固める。
    - 具体戦略（VWAP押し目ロング）は次のステップで実装する。
    """
    if not bars:
        raise BacktestError("bars is empty.")

    strategy = strategy or BaseStrategy()

    # --- policy から必要値を取得 ---
    base_capital = int(policy["capital"]["base_capital"])
    trade_loss_pct = float(policy["risk"]["trade_loss_pct"])
    day_loss_pct = float(policy["risk"]["day_loss_pct"])
    max_positions = int(policy["risk"]["max_positions"])

    session_start = _parse_hhmm(str(policy["time_filter"]["session_start"]))
    session_end = _parse_hhmm(str(policy["time_filter"]["session_end"]))

    exclude_ranges_raw = policy["time_filter"].get("exclude_ranges", [])
    exclude_ranges: List[Tuple[time, time]] = []
    for a, b in exclude_ranges_raw:
        exclude_ranges.append((_parse_hhmm(a), _parse_hhmm(b)))

    slippage_pct = float(policy["strategy"]["slippage_pct"])
    max_trades_per_day = int(policy["limits"]["max_trades_per_day"])

    budget: RiskBudget = calc_risk_budget_yen(base_capital, trade_loss_pct, day_loss_pct)

    # --- 状態 ---
    trades: List[Trade] = []
    has_position = False
    entry_dt: Optional[datetime] = None
    entry_price: float = 0.0
    qty: int = 0

    day_pnl = 0
    day_limit_hit = False

    # ドローダウン計算（確定損益ベース）
    equity = 0
    peak = 0
    max_dd = 0

    # 連敗
    consecutive_losses = 0
    max_consecutive_losses = 0

    # 日付文字列（bars[0] の日付を使う）
    date_str = bars[0].dt.date().isoformat()

    # --- ループ ---
    # 次足始値約定なので i は 0..n-2 まで使う（i+1 が必要）
    for i in range(len(bars) - 1):
        bar = bars[i]
        next_bar = bars[i + 1]

        t = bar.dt.time()

        # 取引時間外／除外時間帯なら戦略を呼ばない（事故防止）
        if not _in_time_range(t, session_start, session_end):
            continue
        if _in_exclude_ranges(t, exclude_ranges):
            continue

        # デイリミットに当たったら当日停止
        if day_limit_hit:
            break

        # トレード上限（事故防止）
        if len(trades) >= max_trades_per_day and (not has_position):
            break

        # 戦略からアクションを受け取る
        sig = strategy.on_bar(i=i, bars=bars, has_position=has_position, policy=policy)

        # 最大ポジション数（通常1）を守る
        if has_position and max_positions <= 0:
            raise BacktestError("invalid max_positions in policy.")
        if (not has_position) and (max_positions < 1):
            raise BacktestError("max_positions must be >= 1 for trading.")

        # --- エントリー ---
        if (not has_position) and sig.action == "enter":
            fill: Fill = market_fill(next_bar_open=next_bar.open, side="buy", slippage_pct=slippage_pct)
            has_position = True
            entry_dt = next_bar.dt
            entry_price = float(fill.price)

            # フェーズ3では qty を最小限の安全設計にする：
            # “1株あたりの損失幅”は次フェーズで戦略側が決める（ATRなど）。
            # ここでは暫定として「1株の許容損失=entry_price*0.005（0.5%）」を仮置きして、
            # qtyが過大にならないようにする。※次のフェーズで正式化する。
            per_share_loss = max(entry_price * 0.005, 1.0)
            qty = int(budget.trade_loss_yen // per_share_loss)
            qty = max(qty, 1)

            continue

        # --- イグジット ---
        if has_position and sig.action == "exit":
            fill = market_fill(next_bar_open=next_bar.open, side="sell", slippage_pct=slippage_pct)
            exit_price = float(fill.price)
            exit_dt = next_bar.dt

            pnl = int((exit_price - entry_price) * qty)
            day_pnl += pnl

            r = calc_r(pnl_yen=pnl, trade_loss_yen=budget.trade_loss_yen)

            trades.append(
                Trade(
                    entry_dt=entry_dt or bars[0].dt,
                    exit_dt=exit_dt,
                    entry_price=entry_price,
                    exit_price=exit_price,
                    qty=qty,
                    pnl_yen=pnl,
                    r=r,
                )
            )

            # 連敗カウント更新
            if pnl < 0:
                consecutive_losses += 1
                max_consecutive_losses = max(max_consecutive_losses, consecutive_losses)
            else:
                consecutive_losses = 0

            # ドローダウン更新（確定損益ベース）
            equity += pnl
            peak = max(peak, equity)
            dd = equity - peak  # 0以下
            max_dd = min(max_dd, dd)  # 最もマイナスが大きいもの

            # デイリミット判定（確定損益のみ）
            if day_pnl <= -budget.day_loss_yen:
                day_limit_hit = True

            # ポジション解消
            has_position = False
            entry_dt = None
            entry_price = 0.0
            qty = 0

            continue

        # hold は何もしない

    # もし日中最後まで持ってたら（フェーズ3では安全側に倒して強制クローズは次で実装）
    # 次フェーズで「14:30以降は成行クローズ」などを正式に入れる。

    return DayResult(
        date_str=date_str,
        trades=trades,
        pnl_yen=day_pnl,
        day_limit_hit=day_limit_hit,
        max_drawdown_yen=max_dd,  # マイナス値（例：-8000）
        max_consecutive_losses=max_consecutive_losses,
    )