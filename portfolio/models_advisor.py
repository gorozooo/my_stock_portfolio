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
        REBALANCE     = "REBALANCE",     "リバランス/分散"
        CUT_LOSERS    = "CUT_LOSERS",    "含み損下位の整理"
        FIX_METADATA  = "FIX_METADATA",  "銘柄の業種タグ整備"

    session = models.ForeignKey(AdviceSession, on_delete=models.CASCADE, related_name="items")
    kind = models.CharField(max_length=32, choices=Kind.choices)
    message = models.CharField(max_length=500)
    score = models.FloatField(default=0.0)
    reasons = models.JSONField(default=list)
    taken = models.BooleanField(default=False)  # 実行済み（UIでトグル）
    outcome = models.JSONField(null=True, blank=True)  # 後日結果 {"reward":..., "before":{...}, "after":{...}}
    created_at = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return f"[{self.kind}] {self.message[:40]}"


class AdvicePolicy(models.Model):
    """
    学習ポリシー（超軽量UCB1バンディット）
    各 kind ごとに “平均報酬” と “試行回数” を更新していく
    """
    kind = models.CharField(max_length=32, unique=True)
    n = models.IntegerField(default=0)                 # 試行回数
    total_reward = models.FloatField(default=0.0)      # 報酬合計
    avg_reward = models.FloatField(default=0.0)        # 平均（冪等のため冗長保持）
    params = models.JSONField(default=dict, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.kind} n={self.n} avg={self.avg_reward:.3f}"