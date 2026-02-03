"""
[FILE] autotrade/models_backtest.py
[PATH] <project_root>/autotrade/models_backtest.py

このファイルは何？
- 「詳細バックテスト / 実トレード共通」で使うログ専用モデルです。
- バックテストも発注ジョブも、必ずこの構造に書き出します。

設計思想：
- 1トレード = 1レコード（絶対）
- 集計値（PF / 勝率 / DD）は保存しない
- 事実だけを保存し、評価は後段サービスで行う
"""

from django.conf import settings
from django.db import models
from django.utils import timezone


# =========================================================
# 1) トレードログ（バックテスト / 本番 共通）
# =========================================================
class AutoTradeExecution(models.Model):
    """
    売買1回分の「事実ログ」

    ・バックテスト
    ・ペーパートレード
    ・本番発注

    すべて同じ構造で保存する。
    """

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

    # 実行モード
    mode = models.CharField(max_length=10, choices=MODE_CHOICES)

    # どの設定で行われたか（再現性の核）
    snapshot = models.ForeignKey(
        "autotrade.AutoTradeSettingSnapshot",
        on_delete=models.PROTECT,
        related_name="executions",
    )

    # 戦略
    strategy = models.CharField(max_length=20, choices=STRATEGY_CHOICES)

    # 銘柄
    ticker = models.CharField(max_length=20)

    # 売買方向（将来ショート対応）
    side = models.CharField(max_length=5, default="LONG")

    # ===== エントリー =====
    entry_at = models.DateTimeField()
    entry_price = models.FloatField()
    size = models.IntegerField(help_text="株数")

    # ===== イグジット =====
    exit_at = models.DateTimeField()
    exit_price = models.FloatField()
    exit_reason = models.CharField(max_length=10, choices=EXIT_REASON_CHOICES)

    # ===== 結果 =====
    pnl_yen = models.IntegerField(help_text="損益（円）")
    rr = models.FloatField(help_text="Risk-Reward")

    holding_minutes = models.IntegerField()

    created_at = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return (
            f"[{self.mode}] {self.ticker} {self.strategy} "
            f"{self.entry_at:%Y-%m-%d %H:%M}"
        )


# =========================================================
# 2) バックテスト実行メタ情報
# =========================================================
class AutoTradeBacktestRunDetail(models.Model):
    """
    バックテスト1回分の「実行メタ」

    ※ 集計値は保存しない
    ※ 実際の中身は AutoTradeExecution 側にある
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="autotrade_backtest_runs",
    )

    snapshot = models.ForeignKey(
        "autotrade.AutoTradeSettingSnapshot",
        on_delete=models.CASCADE,
        related_name="backtest_runs",
    )

    strategy = models.CharField(max_length=20)

    # 例: 20 / 60 / 120
    window_days = models.IntegerField()

    # データ期間
    start_date = models.DateField()
    end_date = models.DateField()

    executed_at = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return (
            f"[BT] {self.strategy} {self.window_days}d "
            f"{self.start_date}~{self.end_date}"
        )