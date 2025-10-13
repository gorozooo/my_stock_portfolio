# portfolio/models_advisor.py
from django.db import models
from django.utils import timezone


class AdviceSession(models.Model):
    """1回のAIアドバイザー分析セッション"""
    created_at = models.DateTimeField(default=timezone.now)
    context_json = models.JSONField(default=dict)  # KPIやセクターなどのスナップショット
    note = models.CharField(max_length=200, blank=True, default="")

    def __str__(self):
        return f"Session {self.id} ({self.created_at:%Y-%m-%d})"


class AdviceItem(models.Model):
    """個別アドバイス"""
    class Kind(models.TextChoices):
        REDUCE_MARGIN = "REDUCE_MARGIN", "信用圧縮"
        TRIM_WINNERS  = "TRIM_WINNERS",  "含み益上位の部分利確"
        ADD_CASH      = "ADD_CASH",      "現金比率引上げ"
        REBALANCE     = "REBALANCE",     "リバランス"
        CUT_LOSERS    = "CUT_LOSERS",    "含み損下位の整理"

    session = models.ForeignKey(AdviceSession, on_delete=models.CASCADE, related_name="items")
    kind = models.CharField(max_length=32, choices=Kind.choices)
    message = models.CharField(max_length=500)
    score = models.FloatField(default=0.0)
    reasons = models.JSONField(default=list)
    taken = models.BooleanField(default=False)  # 実行済み
    outcome = models.JSONField(null=True, blank=True)  # 後日結果
    created_at = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return f"[{self.kind}] {self.message[:40]}"