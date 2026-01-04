# advisor/models_cache.py
from __future__ import annotations
from django.db import models
from django.conf import settings
from django.utils import timezone

class PriceCache(models.Model):
    """個別ティッカーの価格キャッシュ（15-30分TTL想定）"""
    ticker = models.CharField(max_length=16, db_index=True)
    last_price = models.IntegerField()                # 円（四捨五入）
    source = models.CharField(max_length=32, default="yfinance")  # 任意表記
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("ticker",)

    def __str__(self):
        return f"{self.ticker} {self.last_price} @ {self.updated_at.astimezone(timezone.get_current_timezone()).strftime('%Y-%m-%d %H:%M')}"

class BoardCache(models.Model):
    """作戦ボード完成物のキャッシュ（1-3時間TTL or 朝一）"""
    # ユーザー依存の内容に発展する前提で user を置いておく（今はnull許容）
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    payload = models.JSONField()                      # board_apiがそのまま返す形
    generated_at = models.DateTimeField(db_index=True)  # 生成時刻（JST想定）
    ttl_minutes = models.IntegerField(default=180)    # 既定3時間
    note = models.CharField(max_length=64, blank=True, default="")

    class Meta:
        ordering = ["-generated_at"]

    @property
    def is_fresh(self) -> bool:
        return (timezone.now() - self.generated_at) <= timezone.timedelta(minutes=self.ttl_minutes)

    def __str__(self):
        return f"BoardCache {self.generated_at} fresh={self.is_fresh}"