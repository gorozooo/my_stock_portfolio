# aiapp/models/home_deck_snapshot.py
from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils import timezone


class HomeDeckSnapshot(models.Model):
    """
    Homeの“デッキ一式”を、毎朝固定で保存するためのスナップショット。
    - 再現性（同じ朝→同じ表示）
    - 障害時フォールバック（ニュース取得失敗でも保存が残る）
    """
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="home_deck_snapshots",
    )

    # その日の基準日（localdate想定）
    snapshot_date = models.DateField(db_index=True)

    # Homeで描画するデッキ配列（assets/today_plan/news_trends など全部）
    decks = models.JSONField(default=list, blank=True)

    # 生成時刻（UTCで保存。表示はiso文字列でOK）
    generated_at = models.DateTimeField(default=timezone.now, db_index=True)

    # 例: "2026-01-01T06:30:05+09:00"
    as_of = models.CharField(max_length=64, blank=True, default="")

    class Meta:
        unique_together = [("user", "snapshot_date")]
        indexes = [
            models.Index(fields=["user", "snapshot_date"]),
            models.Index(fields=["generated_at"]),
        ]
        ordering = ["-snapshot_date", "-generated_at"]

    def __str__(self) -> str:
        return f"HomeDeckSnapshot({self.user_id}, {self.snapshot_date})"