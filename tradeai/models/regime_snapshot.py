# =========================================================
# [FILE] regime_snapshot.py
# [PATH] <project_root>/tradeai/models/regime_snapshot.py
#
# このファイルは何？
# - その時点の市場地合い判定を保存するモデルです。
# - ロング追い風 / ショート追い風 / 中立 / ノイズ警戒 を持ちます。
# =========================================================

from django.conf import settings
from django.db import models
from django.utils import timezone


class RegimeSnapshot(models.Model):
    class MarketBias(models.TextChoices):
        LONG_TAILWIND = "LONG_TAILWIND", "ロング追い風"
        SHORT_TAILWIND = "SHORT_TAILWIND", "ショート追い風"
        NEUTRAL = "NEUTRAL", "中立"
        NOISY = "NOISY", "ノイズ警戒"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="tradeai_regime_snapshots",
    )

    as_of = models.DateTimeField("判定日時", default=timezone.now, db_index=True)
    market_bias = models.CharField(
        "地合い判定",
        max_length=32,
        choices=MarketBias.choices,
        default=MarketBias.NEUTRAL,
    )

    long_score = models.DecimalField("ロング点数", max_digits=8, decimal_places=2, default=0)
    short_score = models.DecimalField("ショート点数", max_digits=8, decimal_places=2, default=0)

    summary_text = models.TextField("要約", blank=True, default="")
    payload = models.JSONField("詳細データ", default=dict, blank=True)

    created_at = models.DateTimeField("作成日時", auto_now_add=True)

    class Meta:
        ordering = ["-as_of", "-id"]
        indexes = [
            models.Index(fields=["user", "as_of"]),
            models.Index(fields=["user", "market_bias"]),
        ]

    def __str__(self):
        return f"{self.get_market_bias_display()} @ {self.as_of:%Y-%m-%d %H:%M}"