from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.contrib.postgres.fields import ArrayField  # PostgreSQL 用（SQLiteでもJSONで代替可）

# SQLite/その他でも動かすための軽ラッパ
try:
    from django.db.models import JSONField  # Django 3.1+ 標準
except Exception:  # pragma: no cover
    from django.contrib.postgres.fields import JSONField  # 古いDjango用

User = settings.AUTH_USER_MODEL


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    updated_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        self.updated_at = timezone.now()
        return super().save(*args, **kwargs)


class ActionLog(TimeStampedModel):
    user       = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL)
    ticker     = models.CharField(max_length=20, db_index=True)
    policy_id  = models.CharField(max_length=64, blank=True, default="")
    action     = models.CharField(max_length=32, blank=True, default="")   # e.g. save_order / reject
    note       = models.TextField(blank=True, default="")

    def __str__(self):
        return f"{self.ticker} {self.action} ({self.created_at:%Y-%m-%d})"


class Reminder(TimeStampedModel):
    user    = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL)
    ticker  = models.CharField(max_length=20, db_index=True)
    message = models.CharField(max_length=140, blank=True, default="")
    fire_at = models.DateTimeField(db_index=True)

    def __str__(self):
        return f"⏰ {self.ticker} {self.fire_at:%Y-%m-%d %H:%M}"


class WatchEntry(TimeStampedModel):
    STATUS_ACTIVE   = "active"
    STATUS_ARCHIVED = "archived"
    STATUS_CHOICES = (
        (STATUS_ACTIVE, "Active"),
        (STATUS_ARCHIVED, "Archived"),
    )

    user    = models.ForeignKey(User, on_delete=models.CASCADE, related_name="watch_entries")
    ticker  = models.CharField(max_length=20, db_index=True)
    name    = models.CharField(max_length=120, blank=True, default="")
    status  = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_ACTIVE, db_index=True)

    # ユーザーの任意メモ
    note    = models.TextField(blank=True, default="")

    # --- ボードからコピーした説明（サマリ／詳細） ---
    reason_summary = models.TextField(blank=True, default="")
    # JSON: ["半導体テーマが強い（78点）", "出来高が増えている（+35%）", ...]
    reason_details = JSONField(blank=True, null=True)

    # テーマ／AIのメタ
    theme_label = models.CharField(max_length=60, blank=True, default="")
    theme_score = models.FloatField(blank=True, null=True)   # 0-1
    ai_win_prob = models.FloatField(blank=True, null=True)   # 0-1

    # 目標・損切（テキスト互換）
    target_tp = models.CharField(max_length=120, blank=True, default="")
    target_sl = models.CharField(max_length=120, blank=True, default="")

    # === ここから拡張フィールド（将来利用） ===
    overall_score     = models.IntegerField(blank=True, null=True)   # 0-100
    weekly_trend      = models.CharField(max_length=8, blank=True, default="")  # "up"|"flat"|"down"

    entry_price_hint  = models.IntegerField(blank=True, null=True)   # IN 目安（円）
    tp_price          = models.IntegerField(blank=True, null=True)   # 目標価格（円）
    sl_price          = models.IntegerField(blank=True, null=True)   # 損切価格（円）
    tp_pct            = models.FloatField(blank=True, null=True)     # 0-1
    sl_pct            = models.FloatField(blank=True, null=True)     # 0-1

    position_size_hint = models.IntegerField(blank=True, null=True)  # 数量目安（株数）
    in_position        = models.BooleanField(default=False)

    class Meta:
        # ※ 過去の unique_together(user, ticker, status) は衝突源だったため撤去
        indexes = [
            models.Index(fields=["user", "status", "updated_at"]),
            models.Index(fields=["user", "ticker"]),
        ]
        ordering = ["-updated_at", "-id"]

    def __str__(self):
        return f"[{self.status}] {self.ticker} {self.name}"