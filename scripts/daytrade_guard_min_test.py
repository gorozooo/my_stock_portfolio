# -*- coding: utf-8 -*-
"""
ファイル: scripts/daytrade_guard_min_test.py

目的（最小検証）
- ExecutionGuard1m の time_guard が意図どおり動くか確認する。
- あなたの確定仕様：
  - 新規エントリーは 14:25 で止める
  - session_end は 15:30（管理は続ける）
- ここでは「新規エントリー可否」だけを見る（ポジション管理は別）。

期待結果
- 14:24 は allow_entry=True（※他ガードを通る条件なら）
- 14:25 は allow_entry=False（close_range）
"""

from datetime import datetime
from aiapp.services.daytrade.policy_loader import load_policy_yaml
from aiapp.services.daytrade.execution_guard import ExecutionGuard1m, MinuteBar


def make_bar(dt: datetime) -> MinuteBar:
    # 他ガード（VWAP/出来高/フェイク）で落ちないように「通る形」にする
    # - vwap 必須
    # - volume は 3本分必要（volume_enable=True のため）
    # - fake_breakout_bars=2 なので、直近2本で条件を満たす形にする（long想定）
    return MinuteBar(
        dt=dt,
        open=1000.0,
        high=1002.0,
        low=999.0,
        close=1001.0,
        vwap=1000.0,
        volume=1000.0,
    )


def run_case(hh: int, mm: int):
    policy = load_policy_yaml().policy
    guard = ExecutionGuard1m(policy)

    base = datetime(2026, 1, 23, hh, mm, 0)  # 日付は何でもOK（時刻だけ見る）
    bars = [
        make_bar(base),
        make_bar(base),  # fake_breakout用に2本
        make_bar(base),  # volume用に3本
    ]

    side = "long"
    r = guard.check(bars, side=side)
    print(f"[{hh:02d}:{mm:02d}] allow_entry={r.allow_entry} reason={r.reason}")


def main():
    print("=== ExecutionGuard1m time_guard min test ===")
    # 14:24 → 通過して欲しい（他ガードは通るように組んである）
    run_case(14, 24)
    # 14:25 → close_range で必ず落ちて欲しい
    run_case(14, 25)
    # 15:00 → 当然 close_range
    run_case(15, 0)


if __name__ == "__main__":
    main()