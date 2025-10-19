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

# === ブレッドス（地合い）日次スナップショット ===
class BreadthSnapshot(models.Model):
    date = models.DateField(unique=True, db_index=True)
    ad_ratio = models.FloatField(default=1.0)   # 上昇/下落 騰落比
    vol_ratio = models.FloatField(default=1.0)  # 上げ出来高/下げ出来高
    hl_diff = models.FloatField(default=0.0)    # 新高値 − 新安値
    score = models.FloatField(default=0.0)      # -1..+1
    regime = models.CharField(max_length=16, default="NEUTRAL")  # RISK_ON / NEUTRAL / RISK_OFF
    raw = models.JSONField(default=dict, blank=True)             # 入力の生データ保存用
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-date"]

    def __str__(self) -> str:
        return f"{self.date} {self.regime} score={self.score:.2f}"
