# advisor/models_trend.py
from __future__ import annotations

from django.conf import settings  
from django.db import models
from django.utils import timezone

class TrendResult(models.Model):
    """
    作戦ボードの根拠データ（銘柄ごとの日次/週次トレンドなど）
    - asof: 計算対象日（1銘柄1日1レコード想定）
    - win_prob: その銘柄のAI勝率（0-1）
    - weekly_trend: 'up' / 'flat' / 'down'
    - overall_score: 0-100（ win_prob×0.7 + theme_score×0.3 のような合成点を格納しておく）
    - theme: テーマ名とスコア（0-1）
    - entry_price_hint: IN目安（終値など）
    """
    TREND_CHOICES = (
        ("up", "上向き"),
        ("flat", "横ばい"),
        ("down", "下向き"),
    )

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, db_index=True)
    ticker = models.CharField(max_length=16, db_index=True)
    name   = models.CharField(max_length=128, blank=True, default="")
    asof   = models.DateField(db_index=True)

    close_price      = models.IntegerField(null=True, blank=True)   # 参考終値
    entry_price_hint = models.IntegerField(null=True, blank=True)   # IN目安（=closeでもOK）

    weekly_trend   = models.CharField(max_length=8, choices=TREND_CHOICES, default="flat")
    win_prob       = models.FloatField(null=True, blank=True)       # 0.0 - 1.0
    theme_label    = models.CharField(max_length=64, blank=True, default="")
    theme_score    = models.FloatField(null=True, blank=True)       # 0.0 - 1.0
    overall_score  = models.IntegerField(null=True, blank=True)     # 0 - 100

    # 将来拡張（安全にnull許容）
    size_mult      = models.FloatField(null=True, blank=True)       # 強いとき>1.0
    notes          = models.JSONField(default=dict, blank=True)     # 任意の根拠

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    slope_annual = models.FloatField(null=True, blank=True)
    confidence = models.FloatField(default=0.5)
    window_days = models.IntegerField(default=60)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "ticker", "asof"], name="uniq_trend_user_ticker_asof"),
        ]
        indexes = [
            models.Index(fields=["user", "asof", "overall_score"]),
            models.Index(fields=["user", "weekly_trend"]),
        ]
        ordering = ["-asof", "-overall_score", "-updated_at"]

    def __str__(self):
        return f"{self.asof} {self.ticker} {self.overall_score}"
