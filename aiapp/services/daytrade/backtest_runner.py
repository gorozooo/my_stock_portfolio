# -*- coding: utf-8 -*-
"""
ファイル: aiapp/services/daytrade/backtest_runner.py

これは何？
- デイトレ全自動売買の「バックテスト実行エンジン（心臓部）」です。
- 1日分のローソク足（1分足など）を時系列に1本ずつ再生し、
  本番と同じ前提（次足始値約定・スリッページ・デイリミット）で
  トレードした結果を集計します。

このファイルが担当すること
- 時間帯フィルタ（取引開始/終了、除外時間）
- エントリー/イグジットの実行（戦略からの指示に従う）
- 約定価格の決定（execution_sim を使用）
- 確定損益の集計
- デイリミット（1日の最大損失）判定
- ドローダウン、連敗数の計算
- 引け時の強制クローズ

このファイルが担当しないこと
- 戦略ロジックそのもの（VWAP押し目など）
  → strategies.py に分離
- 数量・損失計算の詳細
  → risk_math.py に分離
- データ取得（DB/API）
  → 別サービスで実装

置き場所（重要）
- プロジェクトルート（manage.py がある階層）から見て:
  aiapp/services/daytrade/backtest_runner.py
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from typing import Any, Dict, List, Literal, Optional, Tuple

from .execution_sim import Fill, market_fill
from .risk_math import RiskBudget, calc_r, calc_risk_budget_yen
from .strategies import VWAPPullbackLongStrategy


Side = Literal["long"]


class BacktestError(RuntimeError):
    """バックテスト実行中に前提が崩れた場合の例外。"""


# =========================
# データ構造
# =========================

@dataclass(frozen=True)
class Bar:
    """
    1本の足（最小構成）

    dt:
      足の時刻（datetime）

    open/high/low/close:
      価格

    vwap:
      VWAP（事前計算済みを渡す前提）

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
      約定価格（スリッページ反映後）

    qty:
      株数

    pnl_yen:
      損益（円・確定のみ）

    r:
      R換算（pnl_yen / 1トレード最大損失）
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
    1日分のバックテスト結果（集計用）
    """
    date_str: str
    trades: List[Trade]
    pnl_yen: int
    day_limit_hit: bool
    max_drawdown_yen: int
    max_consecutive_losses: int


# =========================
# 内部ユーティリティ
# =========================

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


# =========================
# 戦略インターフェース
# =========================

@dataclass(frozen=True)
class StrategySignal:
    """
    戦略が返すアクション

    action:
      "enter" / "exit" / "hold"

    reason:
      デバッグ・UI表示用の理由
    """
    action: Literal["enter", "exit", "hold"]
    reason: str = ""


class BaseStrategy:
    """
    戦略の基底クラス（インターフェース）

    on_bar():
      各バーごとに呼ばれ、
      エントリー/イグジット/ホールドを返す
    """

    def on_bar(
        self,
        i: int,
        bars: List[Bar],
        has_position: bool,
        policy: Dict[str, Any],
    ) -> StrategySignal:
        return StrategySignal(action="hold", reason="base strategy (no-op)")


# =========================
# メイン：1日バックテスト
# =========================

def run_backtest_one_day(
    bars: List[Bar],
    policy: Dict[str, Any],
    strategy: Optional[BaseStrategy] = None,
) -> DayResult:
    """
    1日分のバー列を再生してバックテストを実行する。

    重要な前提
    - 次足始値約定（i+1 の open）
    - スリッページあり
    - 確定損益のみ集計
    - デイリミット到達で当日停止
    """
    if not bars:
        raise BacktestError("bars is empty.")

    # 戦略が指定されていなければVWAP押し目ロングを使う
    strategy = strategy or VWAPPullbackLongStrategy()

    # --- policy から設定を取得 ---
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

    budget: RiskBudget = calc_risk_budget_yen(
        base_capital,
        trade_loss_pct,
        day_loss_pct,
    )

    # --- 状態変数 ---
    trades: List[Trade] = []
    has_position = False
    entry_dt: Optional[datetime] = None
    entry_price: float = 0.0
    qty: int = 0

    day_pnl = 0
    day_limit_hit = False

    # ドローダウン（確定損益ベース）
    equity = 0
    peak = 0
    max_dd = 0

    # 連敗数
    consecutive_losses = 0
    max_consecutive_losses = 0

    date_str = bars[0].dt.date().isoformat()

    # =========================
    # バーを1本ずつ再生
    # =========================
    for i in range(len(bars) - 1):
        bar = bars[i]
        next_bar = bars[i + 1]
        t = bar.dt.time()

        # 取引時間外・除外時間帯はスキップ
        if not _in_time_range(t, session_start, session_end):
            continue
        if _in_exclude_ranges(t, exclude_ranges):
            continue

        # デイリミット到達で当日終了
        if day_limit_hit:
            break

        # トレード回数制限
        if len(trades) >= max_trades_per_day and not has_position:
            break

        # 戦略判断
        sig = strategy.on_bar(
            i=i,
            bars=bars,
            has_position=has_position,
            policy=policy,
        )

        # --- エントリー ---
        if not has_position and sig.action == "enter":
            fill: Fill = market_fill(
                next_bar_open=next_bar.open,
                side="buy",
                slippage_pct=slippage_pct,
            )
            has_position = True
            entry_dt = next_bar.dt
            entry_price = float(fill.price)

            # フェーズ3では暫定的な数量計算
            # （次フェーズで risk_math に完全統合）
            per_share_loss = max(entry_price * 0.005, 1.0)
            qty = int(budget.trade_loss_yen // per_share_loss)
            qty = max(qty, 1)
            continue

        # --- イグジット ---
        if has_position and sig.action == "exit":
            fill = market_fill(
                next_bar_open=next_bar.open,
                side="sell",
                slippage_pct=slippage_pct,
            )
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

            # 連敗・DD更新
            if pnl < 0:
                consecutive_losses += 1
                max_consecutive_losses = max(max_consecutive_losses, consecutive_losses)
            else:
                consecutive_losses = 0

            equity += pnl
            peak = max(peak, equity)
            max_dd = min(max_dd, equity - peak)

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

    # =========================
    # 引け強制クローズ
    # =========================
    if has_position and entry_dt is not None:
        last_bar = bars[-1]
        fill = market_fill(
            next_bar_open=last_bar.close,
            side="sell",
            slippage_pct=slippage_pct,
        )
        exit_price = float(fill.price)
        exit_dt = last_bar.dt

        pnl = int((exit_price - entry_price) * qty)
        day_pnl += pnl
        r = calc_r(pnl_yen=pnl, trade_loss_yen=budget.trade_loss_yen)

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

    return DayResult(
        date_str=date_str,
        trades=trades,
        pnl_yen=day_pnl,
        day_limit_hit=day_limit_hit,
        max_drawdown_yen=max_dd,
        max_consecutive_losses=max_consecutive_losses,
    )