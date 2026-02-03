"""
[FILE] autotrade/models.py
[PATH] <project_root>/autotrade/models.py

このファイルは何？
- デイトレ自動売買に関する「状態・設定・検証結果（サマリー）」をDBで管理するモデル群です。

役割：
- DailyState：今日の状態（iPhoneダッシュボード）
- TuningProfile：調整用の作業台
- SettingSnapshot：再現性の核となる固定設定
- BacktestRun：バックテスト結果のサマリー履歴（PF/判定など）
"""

from django.conf import settings
from django.db import models
from django.utils import timezone


# =========================================================
# 1) iPhone 1画面ダッシュボード用（日次状態）
# =========================================================
class AutoTradeDailyState(models.Model):
    date = models.DateField(unique=True)

    gate_level = models.CharField(max_length=10, default="STOP")
    gate_reason = models.TextField(blank=True, default="")

    equity_yen = models.BigIntegerField(
        default=getattr(settings, "AUTOTRADE_BASE_EQUITY_YEN", 1_000_000)
    )
    pnl_day_yen = models.IntegerField(default=0)
    pnl_total_yen = models.IntegerField(default=0)

    strategy = models.CharField(max_length=20, blank=True, default="")
    strategy_decided_at = models.DateTimeField(null=True, blank=True)

    morning_stats = models.JSONField(default=dict, blank=True)
    strategy_decision = models.JSONField(default=dict, blank=True)

    universe = models.JSONField(default=dict, blank=True)
    backtest = models.JSONField(default=dict, blank=True)
    rules = models.JSONField(default=dict, blank=True)

    emergency_stop = models.BooleanField(default=False)
    emergency_stop_reason = models.CharField(max_length=200, blank=True, default="")
    emergency_stopped_at = models.DateTimeField(null=True, blank=True)

    updated_at = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return f"[{self.date}] {self.gate_level} {self.strategy}".strip()


# =========================================================
# 2) 調整用プロファイル
# =========================================================
class AutoTradeTuningProfile(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="autotrade_tuning_profiles",
    )
    name = models.CharField(max_length=100)
    params = models.JSONField()
    is_archived = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"[TUNING] {self.name}"


# =========================================================
# 3) 設定スナップショット（再現性の核）
# =========================================================
class AutoTradeSettingSnapshot(models.Model):
    STATUS_CHOICES = (
        ("DRAFT", "下書き"),
        ("CANDIDATE", "本番候補"),
        ("ACTIVE", "本番採用中"),
        ("RETIRED", "引退"),
    )

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="autotrade_snapshots",
    )

    source_profile = models.ForeignKey(
        AutoTradeTuningProfile,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="snapshots",
    )

    label = models.CharField(max_length=100)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="DRAFT")
    snapshot = models.JSONField()

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"[SNAPSHOT] {self.label} ({self.status})"


# =========================================================
# 4) バックテスト結果サマリー（集計）
# =========================================================
class AutoTradeBacktestRun(models.Model):
    """
    バックテスト結果の「集計サマリー」だけを保存するモデル
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="autotrade_backtests",
    )

    snapshot = models.ForeignKey(
        AutoTradeSettingSnapshot,
        on_delete=models.CASCADE,
        related_name="backtest_summaries",
    )

    universe_version = models.CharField(max_length=100)
    period_set = models.CharField(max_length=20)

    result_summary = models.JSONField()
    gate_result = models.CharField(max_length=10)
    reason_text = models.TextField(blank=True, default="")

    started_at = models.DateTimeField(default=timezone.now)
    finished_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"[BACKTEST-SUM] {self.snapshot.label} {self.gate_result}"
        
# =========================================================
# 詳細バックテスト / 実行ログ（別ファイル定義）
# =========================================================
from .models_backtest import (
    AutoTradeExecution,
    AutoTradeBacktestRunDetail,
)