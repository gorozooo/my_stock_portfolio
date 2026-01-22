# -*- coding: utf-8 -*-
"""
ファイル: aiapp/services/daytrade/minute_collector.py

これは何？
- 場中専用の「1分足コレクタ」。
- 無料データ前提で、ExecutionGuardに渡す最低限の1分足を作る。
- 完璧なOHLCは目指さない（事故回避が目的）。

重要な割り切り
- 欠損したら「作らない」
- 作れない分足は ExecutionGuard が弾く
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class Tick:
    dt: datetime
    price: float
    volume: Optional[float] = None


@dataclass
class MinuteBar:
    dt: datetime
    open: float
    high: float
    low: float
    close: float
    volume: Optional[float] = None
    vwap: Optional[float] = None


class MinuteBarBuilder:
    """
    ティックから1分足を組み立てる。
    """

    def __init__(self):
        self.current_minute: Optional[datetime] = None
        self.open: Optional[float] = None
        self.high: Optional[float] = None
        self.low: Optional[float] = None
        self.close: Optional[float] = None
        self.volume_sum: float = 0.0

    def update(self, tick: Tick) -> Optional[MinuteBar]:
        """
        新しいティックを受け取る。
        分が変わったら MinuteBar を返す。
        """
        minute = tick.dt.replace(second=0, microsecond=0)

        # 初回
        if self.current_minute is None:
            self._start_new(minute, tick)
            return None

        # 同じ分
        if minute == self.current_minute:
            self._update_current(tick)
            return None

        # 分が変わった → 確定
        bar = MinuteBar(
            dt=self.current_minute,
            open=self.open,
            high=self.high,
            low=self.low,
            close=self.close,
            volume=self.volume_sum if self.volume_sum > 0 else None,
            vwap=None,  # 後で計算 or 未使用
        )

        # 新しい分を開始
        self._start_new(minute, tick)
        return bar

    def _start_new(self, minute: datetime, tick: Tick):
        self.current_minute = minute
        self.open = tick.price
        self.high = tick.price
        self.low = tick.price
        self.close = tick.price
        self.volume_sum = tick.volume or 0.0

    def _update_current(self, tick: Tick):
        self.high = max(self.high, tick.price)
        self.low = min(self.low, tick.price)
        self.close = tick.price
        if tick.volume:
            self.volume_sum += tick.volume