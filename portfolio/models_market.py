# -*- coding: utf-8 -*-
from __future__ import annotations
from django.db import models
from django.utils import timezone

class SectorSignal(models.Model):
    """
    1日1行×セクターの強弱スナップショット（yfinance等から自動生成）
    rs_score  : 相対強弱（-1..+1目安、同日セクター横並びz-score→tanh圧縮）
    advdec    : 騰落ブレッドス（将来用）
    vol_ratio : 出来高比（近20/過去60）
    meta      : { "chg5": float, "chg20": float, "base": float, ... }
    """
    date = models.DateField(default=timezone.now, db_index=True)
    sector = models.CharField(max_length=64, db_index=True)  # Holding.sector と一致させる
    rs_score = models.FloatField(default=0.0)
    advdec = models.FloatField(null=True, blank=True)
    vol_ratio = models.FloatField(null=True, blank=True)
    meta = models.JSONField(default=dict, blank=True)

    class Meta:
        unique_together = ("date", "sector")
        indexes = [
            models.Index(fields=["date", "sector"]),
            models.Index(fields=["sector", "date"]),
        ]
        ordering = ["-date", "sector"]

    def __str__(self):
        return f"{self.date} {self.sector} rs={self.rs_score:+.3f}"