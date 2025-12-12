# aiapp/models/behavior_stats.py
from __future__ import annotations

from django.db import models
from django.utils import timezone


class BehaviorStats(models.Model):
    """
    銘柄 × mode_period × mode_aggr ごとの「本番用⭐️」集計結果を保存するテーブル。

    - window_days: 直近何日で集計したか（基本 90）
    - stars: 1〜5（本番表示用）
    - win_rate / avg_r / trades: 検証とデバッグのために保持
    """

    code = models.CharField(max_length=8)
    mode_period = models.CharField(max_length=8)  # "short"/"mid"/"long"
    mode_aggr = models.CharField(max_length=8)    # "aggr"/"norm"/"def"

    window_days = models.IntegerField(default=90)

    trades = models.IntegerField(default=0)
    wins = models.IntegerField(default=0)
    losses = models.IntegerField(default=0)
    flats = models.IntegerField(default=0)

    win_rate = models.FloatField(default=0.0)
    avg_r = models.FloatField(default=0.0)

    score_0_1 = models.FloatField(default=0.0)    # 内部スコア(0..1)
    stars = models.IntegerField(default=3)

    computed_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "aiapp_behavior_stats"
        constraints = [
            models.UniqueConstraint(
                fields=["code", "mode_period", "mode_aggr"],
                name="uq_aiapp_behavior_stats_key",
            )
        ]
        indexes = [
            models.Index(fields=["code", "mode_period", "mode_aggr"]),
            models.Index(fields=["computed_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.code} {self.mode_period}/{self.mode_aggr} stars={self.stars} trades={self.trades}"