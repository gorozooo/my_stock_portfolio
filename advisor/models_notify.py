from __future__ import annotations
from django.db import models
from django.contrib.auth import get_user_model

User = get_user_model()

class NotificationLog(models.Model):
    user = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL)
    ticker = models.CharField(max_length=16, db_index=True)
    reason_key = models.CharField(max_length=64, db_index=True)  # 同一理由の重複抑止キー
    window = models.CharField(max_length=16, default="daily")    # preopen/intraday/afterclose/daily
    message = models.TextField(blank=True)
    sent_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["ticker", "reason_key", "window", "-sent_at"]),
        ]
        ordering = ["-sent_at"]

    def __str__(self) -> str:
        return f"{self.ticker} {self.reason_key} {self.window} {self.sent_at:%Y-%m-%d %H:%M}"