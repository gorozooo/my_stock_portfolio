# =========================================================
# [FILE] learning_snapshot.py
# [PATH] <project_root>/tradeai/models/learning_snapshot.py
#
# このファイルは何？
# - 学習用の「その時点の状態」を保存するモデルです。
# - 特徴量、シグナル、地合い、通知、見送りを保存します。
# =========================================================

from django.conf import settings
from django.db import models
from django.utils import timezone


class LearningSnapshot(models.Model):
    class DirectionChoices(models.TextChoices):
        LONG = "LONG", "ロング"
        SHORT = "SHORT", "ショート"

    class SourceScopeChoices(models.TextChoices):
        HOLDING = "HOLDING", "保有銘柄"
        WATCHLIST = "WATCHLIST", "ウォッチリスト"
        UNIVERSE = "UNIVERSE", "母集団"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="tradeai_learning_snapshots",
    )

    signal_event = models.ForeignKey(
        "tradeai.SignalEvent",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="learning_snapshots",
    )
    demo_trade = models.ForeignKey(
        "tradeai.DemoTrade",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="learning_snapshots",
    )

    ticker = models.CharField("証券コード", max_length=16, db_index=True)
    name = models.CharField("銘柄名", max_length=128, blank=True, default="")

    direction = models.CharField("方向", max_length=8, choices=DirectionChoices.choices)
    source_scope = models.CharField("発生元", max_length=16, choices=SourceScopeChoices.choices)

    snapshot_at = models.DateTimeField("記録日時", default=timezone.now, db_index=True)
    signal_score = models.DecimalField("通知点数", max_digits=8, decimal_places=2, default=0)
    regime_label = models.CharField("地合いラベル", max_length=32, blank=True, default="")

    features_json = models.JSONField("特徴量", default=dict, blank=True)
    signals_json = models.JSONField("シグナル詳細", default=dict, blank=True)

    was_notified = models.BooleanField("通知した", default=False)
    was_entered = models.BooleanField("入った", default=False)

    created_at = models.DateTimeField("作成日時", auto_now_add=True)

    class Meta:
        ordering = ["-snapshot_at", "-id"]
        indexes = [
            models.Index(fields=["user", "snapshot_at"]),
            models.Index(fields=["user", "ticker", "snapshot_at"]),
            models.Index(fields=["direction", "snapshot_at"]),
        ]

    def __str__(self):
        return f"{self.ticker} {self.direction} @ {self.snapshot_at:%Y-%m-%d %H:%M}"

    def save(self, *args, **kwargs):
        if self.ticker:
            self.ticker = self.ticker.upper().strip()
        super().save(*args, **kwargs)