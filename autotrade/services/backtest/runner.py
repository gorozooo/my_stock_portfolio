"""
[FILE] autotrade/services/backtest/runner.py
[PATH] <project_root>/autotrade/services/backtest/runner.py

このファイルは何？
- 詳細バックテストの「司令塔」です。
- Snapshot（固定された設定）を入力として、
  1) エンジンで検証
  2) metrics で数値化
  3) gate で 🟢🟡🔴 判定
  を一気に実行します。

なぜ重要？
- UI / cron / CLI は「ここを呼ぶだけ」で済むようにするため。
- ロジックを分散させない＝後で壊れにくい。

初心者ポイント：
- バックテストを実行したいときは、
  基本このファイルしか見なくてOKです。
"""

from typing import Dict, List
from django.utils import timezone
from django.db import transaction

from autotrade.models import (
    AutoTradeDailyState,
    AutoTradeSettingSnapshot,
)

from autotrade.services.backtest.metrics import summarize_metrics
from autotrade.services.backtest.gate import judge_multi_window

# ※ engine は次のフェーズで実装（今はインターフェース前提）
from autotrade.services.backtest.engine import run_engine_for_window


# =========================================================
# 実行対象の期間（営業日）
# =========================================================
BACKTEST_WINDOWS = (20, 60, 120)


@transaction.atomic
def run_backtest_for_today(
    *,
    snapshot: AutoTradeSettingSnapshot,
    date=None,
) -> AutoTradeDailyState:
    """
    今日のバックテストをすべて実行し、
    AutoTradeDailyState に結果を保存するメイン関数。

    引数:
      snapshot:
        SettingSnapshot（固定された数値設定）
      date:
        対象日（省略時は今日）

    戻り値:
      更新された AutoTradeDailyState
    """

    if date is None:
        date = timezone.localdate()

    # -----------------------------------------------------
    # 今日の状態レコードを取得 or 作成
    # -----------------------------------------------------
    state, _ = AutoTradeDailyState.objects.get_or_create(
        date=date,
        defaults={
            "gate_level": "STOP",
            "gate_reason": "",
        },
    )

    metrics_by_window: Dict[int, Dict[str, float]] = {}
    backtest_raw: Dict[str, Dict] = {}

    # -----------------------------------------------------
    # 各期間（20 / 60 / 120）でバックテスト
    # -----------------------------------------------------
    for window in BACKTEST_WINDOWS:
        # engine は「生の結果」を返すだけ
        engine_result = run_engine_for_window(
            snapshot=snapshot,
            window_days=window,
        )

        pnls: List[float] = engine_result["pnls"]
        equity_curve: List[float] = engine_result["equity_curve"]

        metrics = summarize_metrics(
            pnls=pnls,
            equity_curve=equity_curve,
        )

        metrics_by_window[window] = metrics

        # 生データは JSON にまとめて保存（将来の再解析用）
        backtest_raw[str(window)] = {
            "metrics": metrics,
            "trades": engine_result.get("trades", []),
        }

    # -----------------------------------------------------
    # 🟢🟡🔴 判定
    # -----------------------------------------------------
    gate_result = judge_multi_window(metrics_by_window)

    # -----------------------------------------------------
    # DailyState に保存
    # -----------------------------------------------------
    state.gate_level = gate_result["gate_level"]
    state.gate_reason = "\n".join(gate_result["reasons"])
    state.backtest = backtest_raw
    state.updated_at = timezone.now()
    state.save()

    return state