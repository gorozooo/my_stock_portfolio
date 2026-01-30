"""
[FILE] autotrade/jobs/decide_strategy.py
[PATH] <project_root>/autotrade/jobs/decide_strategy.py

このファイルは何？
- 9:30 に動く「戦略決定＆ゲート確定ジョブ」です。
- 役割：
  1) 今日の戦略（BREAKOUT or VWAP）を決める
  2) 決めた戦略のバックテスト結果を使って、3段階ゲート（FULL/LIGHT/STOP）を決める
  3) その日の運用ルール（同時ポジ、最大回数、RRなど）を確定して保存する

初心者ポイント：
- ここで「今日は稼働する？軽くする？止める？」が確定します。
"""

from datetime import date
from django.conf import settings
from django.utils import timezone

from autotrade.models import AutoTradeDailyState
from autotrade.services.strategy_decider import decide_strategy
from autotrade.services.gate_service import gate_from_backtests
from autotrade.services.rules_service import build_rules_for_today


def run():
    today = date.today()
    state, _ = AutoTradeDailyState.objects.get_or_create(date=today)

    # 1) 戦略決定（現状：暫定ロジック。次フェーズで朝30分の値動き判定に強化）
    strategy = decide_strategy(state)

    state.strategy = strategy
    state.strategy_decided_at = timezone.now()

    # 2) その戦略のバックテスト結果でゲート判定
    bt_all = state.backtest if isinstance(state.backtest, dict) else {}
    bt_strategy = bt_all.get(strategy, {}) if isinstance(bt_all, dict) else {}

    # windowキーは文字列 "20" "60" "120" を使う（JSON互換）
    bt_by_window = {int(w): bt_strategy.get(str(w), {}) for w in settings.AUTOTRADE_BT_WINDOWS}

    gate_level, reason = gate_from_backtests(bt_by_window)
    state.gate_level = gate_level
    state.gate_reason = reason

    # 3) ルール確定（FULL/LIGHT/STOP で自動的に変わる）
    state.rules = build_rules_for_today(
        gate_level=gate_level,
        strategy=strategy,
        equity_yen=state.equity_yen or settings.AUTOTRADE_BASE_EQUITY_YEN,
    )

    state.updated_at = timezone.now()
    state.save()