# -*- coding: utf-8 -*-
from __future__ import annotations
from django.db import models
from django.utils import timezone

class SectorSignal(models.Model):
    """
    1日1レコード/セクターの強弱スコア（外部CSV等から取り込み）
    例: rs_score … セクター相対強弱（-1.0 ~ +1.0）
        advdec  … 騰落比率（-1.0 ~ +1.0、上昇優位なら+）
        vol_ratio … 売買代金/出来高 比（1.0 基準 >1で活況）
    """
    date = models.DateField(db_index=True)
    sector = models.CharField(max_length=64, db_index=True)
    rs_score = models.FloatField(default=0.0)
    advdec = models.FloatField(default=0.0)
    vol_ratio = models.FloatField(default=1.0)
    meta = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        unique_together = (("date", "sector"),)
        ordering = ["-date", "sector"]

    def __str__(self) -> str:
        return f"{self.date} {self.sector} rs={self.rs_score:+.2f}"