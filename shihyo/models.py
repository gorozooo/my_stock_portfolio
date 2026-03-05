"""
[FILE] models.py
[PATH] <project_root>/shihyo/models.py

このファイルは何？
- 日経先物 / ドル円 / VIX の取得結果を「スナップショット」としてDBに保存します。
- 画面は必ず最新スナップショットを表示するので、表示がブレず再現性が高いです。
"""

from django.db import models


class MarketIndicatorSnapshot(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)

    # --- raw values ---
    nikkei_futures_last = models.FloatField(null=True, blank=True)
    nikkei_futures_change = models.FloatField(null=True, blank=True)
    nikkei_futures_change_pct = models.FloatField(null=True, blank=True)

    usdjpy_last = models.FloatField(null=True, blank=True)
    usdjpy_change = models.FloatField(null=True, blank=True)
    usdjpy_change_pct = models.FloatField(null=True, blank=True)

    vix_last = models.FloatField(null=True, blank=True)
    vix_change = models.FloatField(null=True, blank=True)
    vix_change_pct = models.FloatField(null=True, blank=True)

    # --- beginner-friendly labels ---
    nikkei_label = models.CharField(max_length=32, default="", blank=True)
    fx_label = models.CharField(max_length=32, default="", blank=True)
    vix_label = models.CharField(max_length=32, default="", blank=True)

    # --- final action (the important part) ---
    action_title = models.CharField(max_length=64, default="", blank=True)
    action_lines = models.JSONField(default=list, blank=True)

    # --- debug / source ---
    source = models.CharField(max_length=32, default="stooq")
    raw_payload = models.JSONField(default=dict, blank=True)
    error = models.TextField(default="", blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Snapshot {self.created_at:%Y-%m-%d %H:%M}"