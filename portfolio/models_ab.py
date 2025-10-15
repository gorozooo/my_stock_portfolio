from __future__ import annotations
from django.db import models
from django.utils import timezone

class ABExperiment(models.Model):
    """
    例: key='ai_advisor_layout', variants=['A','B']
    """
    key = models.CharField(max_length=64, unique=True, db_index=True)
    variants_json = models.JSONField(default=list)  # ["A","B"]
    enabled = models.BooleanField(default=True)
    note = models.CharField(max_length=200, blank=True, default="")
    created_at = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return f"{self.key} ({'on' if self.enabled else 'off'})"

    @property
    def variants(self):
        v = self.variants_json or []
        return [str(x) for x in v] if isinstance(v, list) else []
    

class ABAssignment(models.Model):
    """
    ユーザー/セッション毎の割当（匿名CookieベースでもOK）
    """
    experiment = models.ForeignKey(ABExperiment, on_delete=models.CASCADE, related_name="assignments")
    identity = models.CharField(max_length=128, db_index=True)  # user_id か cookie_id
    variant = models.CharField(max_length=16)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        unique_together = ("experiment", "identity")

    def __str__(self):
        return f"{self.identity} -> {self.variant}"


class ABEvent(models.Model):
    """
    軽量イベントログ
    name: 'view', 'click_check', 'gen_weekly', 'gen_nextmove', 'conversion' 等
    meta: 任意（{score:..., item_id:...} など）
    """
    experiment = models.ForeignKey(ABExperiment, on_delete=models.SET_NULL, null=True, blank=True)
    identity = models.CharField(max_length=128, db_index=True)
    variant = models.CharField(max_length=16, blank=True, default="")
    name = models.CharField(max_length=32)
    meta = models.JSONField(default=dict, blank=True)
    at = models.DateTimeField(default=timezone.now)

    class Meta:
        indexes = [
            models.Index(fields=["experiment", "variant", "name", "at"]),
            models.Index(fields=["identity", "at"]),
        ]

    def __str__(self):
        return f"{self.name}@{self.variant} {self.at:%Y-%m-%d %H:%M}"