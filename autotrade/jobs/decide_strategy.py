"""
[FILE] autotrade/jobs/decide_strategy.py
[PATH] <project_root>/autotrade/jobs/decide_strategy.py

このファイルは何？
- 9:30 に動く「戦略決定＆ゲート確定ジョブ」です。

役割（jobsの責務はこれだけにする）：
  1) 今日の戦略（BREAKOUT or VWAP）を決める（decision/strategy.py）
  2) その戦略のバックテスト結果から、3段階ゲート（FULL/LIGHT/STOP）を決める（backtest/gate.py）
  3) その日の運用ルール（同時ポジ、最大回数など）を確定して保存する（decision/rules.py）

初心者ポイント：
- job は「時間で動く入口」だけ。
- 中身（判断ロジック）は services に寄せて、後で見返しても迷子にならないようにします。
"""

from datetime import date
from django.conf import settings
from django.utils import timezone

from autotrade.models import AutoTradeDailyState
from autotrade.services.decision.strategy import decide_strategy_for_state
from autotrade.services.backtest.gate import gate_from_backtests
from autotrade.services.decision.rules import build_rules_for_today


def run():
    today = date.today()
    state, _ = AutoTradeDailyState.objects.get_or_create(date=today)

    # 1) 戦略決定（※次フェーズで morning_data_service と正式接続して精度を上げる）
    decision = decide_strategy_for_state(state)
    strategy = decision.strategy

    state.strategy = strategy
    state.strategy_decided_at = timezone.now()

    # 2) その戦略のバックテスト結果でゲート判定
    # state.backtest は
    #   {"BREAKOUT": {"20": {...}, "60": {...}, "120": {...}}, "VWAP": {...}}
    # の形を想定
    bt_all = state.backtest if isinstance(state.backtest, dict) else {}
    bt_strategy = bt_all.get(strategy, {}) if isinstance(bt_all, dict) else {}

    # windowキーは文字列 "20" "60" "120" を使う（JSON互換）
    windows = getattr(settings, "AUTOTRADE_BT_WINDOWS", [20, 60, 120])
    bt_by_window = {int(w): bt_strategy.get(str(w), {}) for w in windows}

    gate_level, reason = gate_from_backtests(bt_by_window)
    state.gate_level = gate_level
    state.gate_reason = reason

    # 3) ルール確定（FULL/LIGHT/STOP で自動的に変わる）
    equity_yen = state.equity_yen or getattr(settings, "AUTOTRADE_BASE_EQUITY_YEN", 1_000_000)
    state.rules = build_rules_for_today(
        gate_level=gate_level,
        strategy=strategy,
        equity_yen=equity_yen,
    )

    state.updated_at = timezone.now()
    state.save()