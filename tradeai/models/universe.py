# =========================================================
# [FILE] universe.py
# [PATH] <project_root>/tradeai/models/universe.py
#
# このファイルは何？
# - tradeai が実際に監視対象として使う「対象銘柄集合」を保存するモデルです。
# - 日経225 / TOPIX / ウォッチリスト / 保有銘柄 を統合した結果を持ちます。
# =========================================================

from django.conf import settings
from django.db import models


class UniverseTicker(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="tradeai_universe_tickers",
    )

    ticker = models.CharField("証券コード", max_length=16, db_index=True)
    name = models.CharField("銘柄名", max_length=128, blank=True, default="")
    market = models.CharField("市場", max_length=8, default="JP")

    in_nikkei225 = models.BooleanField("日経225採用", default=False)
    in_topix = models.BooleanField("TOPIX採用", default=False)
    from_watchlist = models.BooleanField("ウォッチリスト由来", default=False)
    from_holding = models.BooleanField("保有銘柄由来", default=False)

    is_active = models.BooleanField("有効", default=True)
    priority = models.PositiveSmallIntegerField("優先度", default=100)
    memo = models.CharField("メモ", max_length=255, blank=True, default="")

    created_at = models.DateTimeField("作成日時", auto_now_add=True)
    updated_at = models.DateTimeField("更新日時", auto_now=True)

    class Meta:
        ordering = ["priority", "ticker"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "ticker"],
                name="uniq_tradeai_universe_user_ticker",
            ),
        ]
        indexes = [
            models.Index(fields=["user", "is_active"]),
            models.Index(fields=["user", "priority"]),
            models.Index(fields=["ticker", "is_active"]),
        ]

    def __str__(self):
        return f"{self.ticker} / {self.name or '-'}"

    def save(self, *args, **kwargs):
        if self.ticker:
            self.ticker = self.ticker.upper().strip()
        super().save(*args, **kwargs)