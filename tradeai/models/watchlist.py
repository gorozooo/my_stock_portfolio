# =========================================================
# [FILE] watchlist.py
# [PATH] <project_root>/tradeai/models/watchlist.py
#
# このファイルは何？
# - ユーザーが手動で監視したい銘柄を保存するモデルです。
# - ロング監視 / ショート監視 / 通知ON/OFF を持ちます。
# =========================================================

from django.conf import settings
from django.db import models


class WatchlistItem(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="tradeai_watchlist_items",
    )

    ticker = models.CharField("証券コード", max_length=16, db_index=True)
    name = models.CharField("銘柄名", max_length=128, blank=True, default="")
    market = models.CharField("市場", max_length=8, default="JP")

    long_enabled = models.BooleanField("ロング監視", default=True)
    short_enabled = models.BooleanField("ショート監視", default=True)
    notify_enabled = models.BooleanField("通知ON", default=True)
    is_active = models.BooleanField("有効", default=True)

    priority = models.PositiveSmallIntegerField("優先度", default=100)
    memo = models.TextField("メモ", blank=True, default="")

    created_at = models.DateTimeField("作成日時", auto_now_add=True)
    updated_at = models.DateTimeField("更新日時", auto_now=True)

    class Meta:
        ordering = ["priority", "ticker"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "ticker"],
                name="uniq_tradeai_watchlist_user_ticker",
            ),
        ]
        indexes = [
            models.Index(fields=["user", "is_active"]),
            models.Index(fields=["user", "priority"]),
            models.Index(fields=["ticker"]),
        ]

    def __str__(self):
        return f"{self.ticker} / {self.name or '-'}"

    def save(self, *args, **kwargs):
        if self.ticker:
            self.ticker = self.ticker.upper().strip()
        super().save(*args, **kwargs)