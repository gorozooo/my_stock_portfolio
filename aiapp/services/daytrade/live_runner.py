# -*- coding: utf-8 -*-
"""
ファイル: aiapp/services/daytrade/live_runner.py

これは何？
- 5分足のシグナル（SignalStrategy）と
  1分足の執行ガード（ExecutionGuard1m）をつなぐ「本番ランナー」。
- 朝の Judge が GO のときのみ起動される想定。

責務（重要）
- 5分足で「入る候補」を検知
- 1分足を数本集めて ExecutionGuard1m に渡す
- OKなら発注、NGなら見送り
- 判断ログを残す（後追い可能）
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional
from datetime import datetime, timedelta

from .execution_guard import ExecutionGuard1m, MinuteBar, GuardResult


# ====== シグナル結果（5分足側から来る） ======

@dataclass
class Signal:
    side: str                 # "long" or "short"
    entry_price: float
    stop_price: float
    take_profit_price: float
    max_hold_minutes: int
    planned_risk_yen: float   # 1トレードあたりの想定損失（円）


# ====== 発注インターフェース（後で差し替え） ======

class OrderExecutor:
    """
    実際の発注を行うクラスのIF。
    今はダミー。後で楽天/SBI等に差し替える。
    """
    def place_market_order(self, side: str, qty: int):
        print(f"[ORDER] market {side} qty={qty}")

    def close_position(self):
        print("[ORDER] close position")


# ====== Live Runner ======

class LiveRunner:
    """
    本番用ランナー。
    """

    def __init__(
        self,
        policy: dict,
        executor: OrderExecutor,
    ):
        self.policy = policy
        self.executor = executor
        self.guard = ExecutionGuard1m(policy)

        self.position_open = False
        self.position_side: Optional[str] = None
        self.entry_price: Optional[float] = None
        self.entry_time: Optional[datetime] = None
        self.signal: Optional[Signal] = None

        self.bars_1m: List[MinuteBar] = []

        # --- stop幅下限（浅すぎるstopを弾く：補正しない） ---
        risk = self.policy.get("risk", {}) or {}
        try:
            self.min_stop_pct = float(risk.get("min_stop_pct", 0.0) or 0.0)
        except Exception:
            self.min_stop_pct = 0.0
        try:
            self.min_stop_yen = float(risk.get("min_stop_yen", 0.0) or 0.0)
        except Exception:
            self.min_stop_yen = 0.0

        if self.min_stop_pct < 0:
            self.min_stop_pct = 0.0
        if self.min_stop_yen < 0:
            self.min_stop_yen = 0.0

    # ---------- 5分足シグナル受信 ----------

    def on_signal(self, signal: Signal):
        """
        5分足戦略から呼ばれる。
        """
        if self.position_open:
            return  # 既にポジションがあるなら無視

        self.signal = signal
        self.bars_1m.clear()

        print(f"[SIGNAL] side={signal.side} entry={signal.entry_price}")

    # ---------- 1分足更新 ----------

    def on_minute_bar(self, bar: MinuteBar):
        """
        場中、1分ごとに呼ばれる。
        """
        # まだシグナルが無い
        if self.signal is None:
            return

        # 1分足を貯める
        self.bars_1m.append(bar)

        # ガード判定（最低限たまったら）
        result = self.guard.check(self.bars_1m, self.signal.side)

        if not result.allow_entry:
            # NG理由はログ用途（今はprint）
            print(f"[GUARD] skip: {result.reason}")
            return

        # ---- エントリー ----
        self._enter_position(bar)

    # ---------- エントリー ----------

    def _enter_position(self, bar: MinuteBar):
        """
        成行エントリー。
        """
        # --- stop幅が浅すぎる場合は見送り（補正しない） ---
        if self.signal is None:
            return

        entry_px = float(bar.close)
        stop_px = float(self.signal.stop_price)

        min_stop = max(entry_px * self.min_stop_pct, self.min_stop_yen)
        if min_stop > 0:
            if abs(entry_px - stop_px) < min_stop:
                print(
                    f"[ENTRY] skip: stop_too_tight "
                    f"entry={entry_px:.4f} stop={stop_px:.4f} "
                    f"stop_width={abs(entry_px-stop_px):.4f} < min_stop={min_stop:.4f}"
                )
                # このシグナルは無効化して次を待つ（1回の候補に固執しない）
                self.signal = None
                self.bars_1m.clear()
                return

        qty = self._calc_qty(
            planned_risk_yen=self.signal.planned_risk_yen,
            entry_price=entry_px,
            stop_price=stop_px,
        )

        if qty <= 0:
            print("[ENTRY] qty=0 skip")
            self.signal = None
            self.bars_1m.clear()
            return

        self.executor.place_market_order(self.signal.side, qty)

        self.position_open = True
        self.position_side = self.signal.side
        self.entry_price = entry_px
        self.entry_time = bar.dt

        print(
            f"[ENTRY] side={self.position_side} "
            f"price={self.entry_price} qty={qty}"
        )

    # ---------- ポジション管理 ----------

    def on_minute_bar_position(self, bar: MinuteBar):
        """
        ポジション保有中の1分足処理。
        """
        if not self.position_open:
            return

        if self.signal is None:
            return

        # 早期撤退チェック
        # NOTE: ExecutionGuard1m.should_early_exit は qty を受ける実装になっている前提
        # ここでは「想定損失円 planned_risk_yen」を基準に、qty込みの逆行円で判定する
        if self.entry_price is not None:
            if self.guard.should_early_exit(
                entry_price=self.entry_price,
                current_price=bar.close,
                planned_risk_yen=self.signal.planned_risk_yen,
                side=self.position_side,
                qty=self._current_qty_like_live_runner(),
            ):
                print("[EXIT] early_stop")
                self._exit_position()
                return

        # 時間切れ
        if self.entry_time is not None:
            elapsed = (bar.dt - self.entry_time).total_seconds() / 60.0
            if elapsed >= self.signal.max_hold_minutes:
                print("[EXIT] time_limit")
                self._exit_position()
                return

        # 利確・損切り（価格ベース）
        if self.position_side == "long":
            if bar.low <= self.signal.stop_price:
                print("[EXIT] stop_loss")
                self._exit_position()
                return
            if bar.high >= self.signal.take_profit_price:
                print("[EXIT] take_profit")
                self._exit_position()
                return
        else:
            if bar.high >= self.signal.stop_price:
                print("[EXIT] stop_loss")
                self._exit_position()
                return
            if bar.low <= self.signal.take_profit_price:
                print("[EXIT] take_profit")
                self._exit_position()
                return

    # ---------- 決済 ----------

    def _exit_position(self):
        self.executor.close_position()

        self.position_open = False
        self.position_side = None
        self.entry_price = None
        self.entry_time = None
        self.signal = None
        self.bars_1m.clear()

    # ---------- 数量計算 ----------

    def _calc_qty(
        self,
        planned_risk_yen: float,
        entry_price: float,
        stop_price: float,
    ) -> int:
        """
        想定損失から数量を逆算。
        """
        per_share_risk = abs(entry_price - stop_price)
        if per_share_risk <= 0:
            return 0

        qty = int(planned_risk_yen / per_share_risk)
        return max(qty, 0)

    def _current_qty_like_live_runner(self) -> int:
        """
        早期撤退判定に渡すqty（LiveRunnerの計算方式と同型）。
        ここでは「entry_price と stop_price から qty を再計算」する。
        - 実運用で約定数量が取れるようになったら、それを使うのが最終形。
        """
        if not self.position_open:
            return 0
        if self.signal is None:
            return 0
        if self.entry_price is None:
            return 0

        return self._calc_qty(
            planned_risk_yen=self.signal.planned_risk_yen,
            entry_price=float(self.entry_price),
            stop_price=float(self.signal.stop_price),
        )