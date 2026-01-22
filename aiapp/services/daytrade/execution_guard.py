# -*- coding: utf-8 -*-
"""
ファイル: aiapp/services/daytrade/execution_guard.py

これは何？
- 1分足専用の「執行ガード（ExecutionGuard1m）」。
- 5分足で出たシグナルに対して、
  「今この1分で入っていいか？」を判定する。
- 勝たせるロジックではない。
  事故を避けるための NO フィルタ専用。

設計思想（超重要）
- 1つでも NG が出たら「入らない」。
- 出来高は GO 条件に使わない。NO 条件のみ。
- 無料データ／欠損前提で安全側に倒す。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional
from datetime import time


# ====== 入力バー（1分足） ======

@dataclass
class MinuteBar:
    dt: object            # datetime
    open: float
    high: float
    low: float
    close: float
    vwap: Optional[float] = None
    volume: Optional[float] = None


# ====== 判定結果 ======

@dataclass
class GuardResult:
    allow_entry: bool
    reason: str


# ====== Execution Guard 本体 ======

class ExecutionGuard1m:
    """
    1分足の執行ガード。

    使い方：
      guard = ExecutionGuard1m(policy)
      result = guard.check(bars_1m, side="long")
      if result.allow_entry:
          エントリー
      else:
          見送り
    """

    def __init__(self, policy: dict):
        self.policy = policy
        self.exec_cfg = policy.get("exec_guards", {})

        self._load_config()

    # ---------- 設定読み込み ----------

    def _load_config(self):
        # 時間ガード
        self.enable = self.exec_cfg.get("enable", True)

        # 価格ガード
        self.require_above_vwap = (
            self.exec_cfg.get("price_filters", {})
            .get("require_above_vwap", True)
        )

        self.fake_breakout_bars = (
            self.exec_cfg.get("price_filters", {})
            .get("fake_breakout_bars", 2)
        )

        # 出来高ガード（NO条件のみ）
        vol_cfg = self.exec_cfg.get("volume_filters", {})
        self.volume_enable = vol_cfg.get("enable", False)
        self.volume_mode = vol_cfg.get("mode", "NO_FILTER")

        self.min_vol_ratio = vol_cfg.get("min_ratio_vs_avg", 0.3)
        self.max_spike_ratio = vol_cfg.get("max_spike_ratio", 3.0)

        # 早期撤退
        early = self.exec_cfg.get("early_stop", {})
        self.early_stop_enable = early.get("enable", True)
        self.max_adverse_r = early.get("max_adverse_r", 0.5)

    # ---------- メイン判定 ----------

    def check(self, bars: List[MinuteBar], side: str) -> GuardResult:
        """
        bars: 直近の1分足（最低2〜3本）
        side: "long" or "short"
        """
        if not self.enable:
            return GuardResult(True, "guard_disabled")

        if len(bars) < max(2, self.fake_breakout_bars):
            return GuardResult(False, "not_enough_bars")

        # 判定順は絶対に変えない
        r = self._time_guard(bars[-1])
        if not r.allow_entry:
            return r

        r = self._price_guard(bars[-1], side)
        if not r.allow_entry:
            return r

        r = self._volume_guard(bars)
        if not r.allow_entry:
            return r

        r = self._fake_breakout_guard(bars, side)
        if not r.allow_entry:
            return r

        return GuardResult(True, "all_guards_passed")

    # ---------- 各ガード ----------

    def _time_guard(self, bar: MinuteBar) -> GuardResult:
        """
        薄い時間帯を切る。
        """
        t = bar.dt.time()

        # 寄り直後（例：9:15〜9:17）
        if time(9, 15) <= t < time(9, 17):
            return GuardResult(False, "open_range")

        # 昼休み前後
        if time(11, 25) <= t < time(12, 35):
            return GuardResult(False, "lunch_range")

        # 引け前
        if t >= time(14, 25):
            return GuardResult(False, "close_range")

        return GuardResult(True, "time_ok")

    def _price_guard(self, bar: MinuteBar, side: str) -> GuardResult:
        """
        VWAP位置チェック。
        """
        if bar.vwap is None:
            return GuardResult(False, "no_vwap")

        if side == "long":
            if self.require_above_vwap and bar.close < bar.vwap:
                return GuardResult(False, "below_vwap")
        else:
            if self.require_above_vwap and bar.close > bar.vwap:
                return GuardResult(False, "above_vwap")

        return GuardResult(True, "price_ok")

    def _volume_guard(self, bars: List[MinuteBar]) -> GuardResult:
        """
        出来高の NO フィルタ。
        """
        if not self.volume_enable:
            return GuardResult(True, "volume_guard_disabled")

        vols = [b.volume for b in bars if b.volume is not None]
        if len(vols) < 3:
            return GuardResult(False, "volume_missing")

        recent = vols[-1]
        avg = sum(vols[:-1]) / max(1, len(vols) - 1)

        if avg <= 0:
            return GuardResult(False, "volume_invalid")

        ratio = recent / avg

        if ratio < self.min_vol_ratio:
            return GuardResult(False, "volume_too_thin")

        if ratio > self.max_spike_ratio:
            return GuardResult(False, "volume_spike")

        return GuardResult(True, "volume_ok")

    def _fake_breakout_guard(self, bars: List[MinuteBar], side: str) -> GuardResult:
        """
        フェイクブレイク回避。
        """
        n = self.fake_breakout_bars
        recent = bars[-n:]

        if side == "long":
            highs = [b.high for b in recent]
            closes = [b.close for b in recent]
            if not (highs[-1] >= max(highs[:-1]) and closes[-1] >= closes[-2]):
                return GuardResult(False, "fake_breakout_long")
        else:
            lows = [b.low for b in recent]
            closes = [b.close for b in recent]
            if not (lows[-1] <= min(lows[:-1]) and closes[-1] <= closes[-2]):
                return GuardResult(False, "fake_breakout_short")

        return GuardResult(True, "breakout_ok")

    # ---------- 早期撤退（エントリー後） ----------

    def should_early_exit(
        self,
        entry_price: float,
        current_price: float,
        planned_risk_yen: float,
        side: str,
    ) -> bool:
        """
        エントリー後の即撤退判定。
        """
        if not self.early_stop_enable:
            return False

        if planned_risk_yen <= 0:
            return False

        if side == "long":
            adverse = entry_price - current_price
        else:
            adverse = current_price - entry_price

        r = adverse / planned_risk_yen

        return r >= self.max_adverse_r