# =========================================================
# [FILE] signal_event.py
# [PATH] <project_root>/tradeai/models/signal_event.py
#
# このファイルは何？
# - 発生したシグナル通知候補を保存するモデルです。
# - 保有銘柄 / ウォッチリスト / 母集団スクリーニング 由来を持ちます。
# =========================================================

from django.conf import settings
from django.db import models
from django.utils import timezone


class SignalEvent(models.Model):
    class ScopeChoices(models.TextChoices):
        HOLDING = "HOLDING", "保有銘柄"
        WATCHLIST = "WATCHLIST", "ウォッチリスト"
        UNIVERSE = "UNIVERSE", "母集団"

    class DirectionChoices(models.TextChoices):
        LONG = "LONG", "ロング"
        SHORT = "SHORT", "ショート"

    class LevelChoices(models.TextChoices):
        REFERENCE = "REFERENCE", "参考"
        ATTENTION = "ATTENTION", "注目"
        STRONG = "STRONG", "強い"

    class StatusChoices(models.TextChoices):
        OPEN = "OPEN", "有効"
        RESOLVED = "RESOLVED", "解決済み"
        DISMISSED = "DISMISSED", "見送り"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="tradeai_signal_events",
    )

    holding = models.ForeignKey(
        "portfolio.Holding",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="tradeai_signal_events",
    )
    watchlist_item = models.ForeignKey(
        "tradeai.WatchlistItem",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="signal_events",
    )

    ticker = models.CharField("証券コード", max_length=16, db_index=True)
    name = models.CharField("銘柄名", max_length=128, blank=True, default="")

    scope = models.CharField("発生元", max_length=16, choices=ScopeChoices.choices)
    direction = models.CharField("方向", max_length=8, choices=DirectionChoices.choices)
    level = models.CharField("強度", max_length=16, choices=LevelChoices.choices)
    status = models.CharField(
        "状態",
        max_length=16,
        choices=StatusChoices.choices,
        default=StatusChoices.OPEN,
    )

    score_total = models.DecimalField("総合点", max_digits=8, decimal_places=2, default=0)
    regime_label = models.CharField("地合いラベル", max_length=32, blank=True, default="")
    reason_text = models.TextField("理由テキスト", blank=True, default="")
    signals_payload = models.JSONField("シグナル詳細", default=dict, blank=True)

    event_at = models.DateTimeField("発生日時", default=timezone.now, db_index=True)
    resolved_at = models.DateTimeField("終了日時", null=True, blank=True)
    created_at = models.DateTimeField("作成日時", auto_now_add=True)

    class Meta:
        ordering = ["-event_at", "-id"]
        indexes = [
            models.Index(fields=["user", "event_at"]),
            models.Index(fields=["user", "ticker", "event_at"]),
            models.Index(fields=["user", "scope", "status"]),
            models.Index(fields=["direction", "level"]),
        ]

    def __str__(self):
        return f"{self.ticker} {self.direction} {self.level}"

    def save(self, *args, **kwargs):
        if self.ticker:
            self.ticker = self.ticker.upper().strip()
        super().save(*args, **kwargs)