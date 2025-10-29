from __future__ import annotations
from django.db import models
from django.utils import timezone


class IndicatorSnapshot(models.Model):
    """
    全銘柄×日付のテクニカル指標を保持するスナップショット。
    ポリシー判定やトレンド解析のベースとなる。
    """
    REGIME_CHOICES = [
        ("trend", "トレンド"),
        ("range", "レンジ"),
        ("mixed", "混合"),
    ]

    ticker = models.CharField(max_length=16, db_index=True)   # 証券コード（例: 8035.T）
    asof = models.DateField(db_index=True)                    # 対象日（例: 2025-10-29）

    # 基本価格情報
    close = models.FloatField(null=True, blank=True)
    ema20 = models.FloatField(null=True, blank=True)
    ema50 = models.FloatField(null=True, blank=True)
    ema75 = models.FloatField(null=True, blank=True)

    # テクニカル指標
    rsi14 = models.FloatField(null=True, blank=True)
    adx14 = models.FloatField(null=True, blank=True)
    atr14 = models.FloatField(null=True, blank=True)

    # 自動レジーム判定（AIが参照する）
    regime_hint = models.CharField(max_length=16, choices=REGIME_CHOICES, default="mixed")

    # メタ情報
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["ticker", "asof"], name="uniq_indicator_ticker_asof"),
        ]
        indexes = [
            models.Index(fields=["asof", "ticker"]),
            models.Index(fields=["regime_hint"]),
        ]
        ordering = ["-asof", "ticker"]

    def __str__(self):
        return f"{self.asof} {self.ticker} ({self.regime_hint})"

    @classmethod
    def infer_regime(cls, ema20: float, ema50: float, adx14: float) -> str:
        """emaとadxからレジーム推定"""
        if adx14 is None or ema20 is None or ema50 is None:
            return "mixed"
        if adx14 > 25 and ema20 > ema50:
            return "trend"
        elif adx14 < 20 and abs(ema20 - ema50) / ema50 < 0.01:
            return "range"
        else:
            return "mixed"

    def update_regime(self):
        """自身のema/adxから自動でregime_hintを更新"""
        self.regime_hint = self.infer_regime(self.ema20 or 0, self.ema50 or 0, self.adx14 or 0)
        self.updated_at = timezone.now()
        self.save()