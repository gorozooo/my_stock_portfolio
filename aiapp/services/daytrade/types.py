# -*- coding: utf-8 -*-
"""
ファイル: aiapp/services/daytrade/types.py

これは何？
- daytrade（デイトレ全自動）で共通に使う「型（データ構造）」をまとめたファイルです。
- backtest_runner.py（実行エンジン）と strategies.py（戦略）が、お互いを直接 import すると
  循環importが起きて壊れるため、共通部分をここに分離します。

置き場所（重要）
- プロジェクトルート（manage.py がある階層）から見て:
  aiapp/services/daytrade/types.py

入っているもの
- Bar: ローソク足1本
- StrategySignal: 戦略が返す enter/exit/hold
- BaseStrategy: 戦略のインターフェース
- Trade: 1トレードの確定結果
- DayResult: 1日分の集計結果
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Literal


@dataclass(frozen=True)
class Bar:
    """
    1本の足（最小構成）

    dt:
      足の時刻（datetime）

    open/high/low/close:
      価格

    vwap:
      VWAP（事前計算済みを渡す前提。将来は別サービスで計算して付与してもOK）

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

    exit_reason:
      決済理由（運用の検証・改善のための最重要ログ）
      例:
        - "stop_loss"
        - "strategy_exit(close_below_vwap)"
        - "take_profit"
        - "time_limit"
        - "force_close_end_of_day"
        - "unknown"
    """
    entry_dt: datetime
    exit_dt: datetime
    entry_price: float
    exit_price: float
    qty: int
    pnl_yen: int
    r: float
    exit_reason: str = ""


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