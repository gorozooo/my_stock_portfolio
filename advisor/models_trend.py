# advisor/models_trend.py
from __future__ import annotations
from django.db import models

class TrendResult(models.Model):
    """
    1銘柄×日付のトレンド要約（日次で1行想定）
    """
    ticker = models.CharField(max_length=16, db_index=True)
    as_of  = models.DateField(db_index=True)

    # 直近N日での回帰傾き（対数価格の傾きを年率換算した係数など、単位は自由）
    slope_annual = models.FloatField(null=True, blank=True)

    # 週足の向き：up / flat / down
    weekly_trend = models.CharField(max_length=8, default="flat")

    # 信頼度 0.0〜1.0（データ量や決定係数などから算出）
    confidence = models.FloatField(default=0.5)

    # 補足（任意）
    window_days = models.IntegerField(default=60)
    note = models.CharField(max_length=200, blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = (("ticker", "as_of"),)
        indexes = [models.Index(fields=["ticker", "as_of"])]

    def __str__(self):
        return f"{self.ticker} {self.as_of} {self.weekly_trend} (conf={self.confidence:.2f})"