# =========================================================
# [FILE] notify_log.py
# [PATH] <project_root>/tradeai/models/notify_log.py
#
# このファイルは何？
# - 通知送信履歴を保存するモデルです。
# - 重複通知の抑制や、成功/失敗の記録に使います。
# =========================================================

from django.conf import settings
from django.db import models
from django.utils import timezone


class NotifyLog(models.Model):
    class DirectionChoices(models.TextChoices):
        LONG = "LONG", "ロング"
        SHORT = "SHORT", "ショート"
        BOTH = "BOTH", "両方"

    class LevelChoices(models.TextChoices):
        REFERENCE = "REFERENCE", "参考"
        ATTENTION = "ATTENTION", "注目"
        STRONG = "STRONG", "強い"

    class ChannelChoices(models.TextChoices):
        LINE = "LINE", "LINE"
        APP = "APP", "APP"
        EMAIL = "EMAIL", "EMAIL"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="tradeai_notify_logs",
    )

    ticker = models.CharField("証券コード", max_length=16, db_index=True)
    name = models.CharField("銘柄名", max_length=128, blank=True, default="")

    direction = models.CharField("方向", max_length=8, choices=DirectionChoices.choices)
    level = models.CharField("強度", max_length=16, choices=LevelChoices.choices)
    channel = models.CharField("通知先", max_length=16, choices=ChannelChoices.choices, default=ChannelChoices.LINE)

    dedupe_key = models.CharField("重複抑制キー", max_length=128, blank=True, default="")
    message_text = models.TextField("通知本文", blank=True, default="")
    was_sent = models.BooleanField("送信成功", default=False)
    response_text = models.TextField("応答", blank=True, default="")

    sent_at = models.DateTimeField("送信日時", default=timezone.now, db_index=True)
    created_at = models.DateTimeField("作成日時", auto_now_add=True)

    class Meta:
        ordering = ["-sent_at", "-id"]
        indexes = [
            models.Index(fields=["user", "sent_at"]),
            models.Index(fields=["user", "ticker", "sent_at"]),
            models.Index(fields=["dedupe_key"]),
        ]

    def __str__(self):
        return f"{self.ticker} {self.level} {self.channel}"

    def save(self, *args, **kwargs):
        if self.ticker:
            self.ticker = self.ticker.upper().strip()
        super().save(*args, **kwargs)