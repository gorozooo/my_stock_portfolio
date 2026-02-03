"""
[FILE] autotrade/models_backtest.py
[PATH] <project_root>/autotrade/models_backtest.py

このファイルは何？
- 詳細バックテスト / ペーパー / 本番トレード共通の「事実ログ」を保存するモデル群です。

設計原則：
- 1トレード = 1レコード（絶対）
- 集計値は保存しない
- 再評価・再集計は後段サービスで行う
"""

from django.conf import settings
from django.db import models
from django.utils import timezone


# =========================================================
# 1) 売買1回分の事実ログ
# =========================================================
class AutoTradeExecution(models.Model):
    MODE_CHOICES = (
        ("BACKTEST", "バックテスト"),
        ("PAPER", "ペーパー"),
        ("LIVE", "本番"),
    )

    STRATEGY_CHOICES = (
        ("BREAKOUT", "ブレイクアウト"),
        ("VWAP", "VWAP押し目"),
    )

    EXIT_REASON_CHOICES = (
        ("TP", "利確"),
        ("SL", "損切"),
        ("TIME", "時間切れ"),
        ("FORCE", "強制決済"),
        ("EOD", "引け決済"),
    )

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="autotrade_executions",
    )

    snapshot = models.ForeignKey(
        "autotrade.AutoTradeSettingSnapshot",
        on_delete=models.PROTECT,
        related_name="executions",
    )

    mode = models.CharField(max_length=10, choices=MODE_CHOICES)
    strategy = models.CharField(max_length=20, choices=STRATEGY_CHOICES)
    ticker = models.CharField(max_length=20)
    side = models.CharField(max_length=5, default="LONG")

    entry_at = models.DateTimeField()
    entry_price = models.FloatField()
    size = models.IntegerField()

    exit_at = models.DateTimeField()
    exit_price = models.FloatField()
    exit_reason = models.CharField(max_length=10, choices=EXIT_REASON_CHOICES)

    pnl_yen = models.IntegerField()
    rr = models.FloatField()
    holding_minutes = models.IntegerField()

    created_at = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return f"[{self.mode}] {self.ticker} {self.strategy}"


# =========================================================
# 2) 詳細バックテスト実行メタ（集計なし）
# =========================================================
class AutoTradeBacktestRunDetail(models.Model):
    """
    詳細バックテスト1回分の「実行メタ情報」
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="autotrade_backtest_run_details",
    )

    snapshot = models.ForeignKey(
        "autotrade.AutoTradeSettingSnapshot",
        on_delete=models.CASCADE,
        related_name="backtest_run_details",
    )

    strategy = models.CharField(max_length=20)
    window_days = models.IntegerField()

    start_date = models.DateField()
    end_date = models.DateField()

    executed_at = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return f"[BT-DETAIL] {self.strategy} {self.window_days}d"