# =========================================================
# [FILE] candidate_snapshot.py
# [PATH] <project_root>/tradeai/models/candidate_snapshot.py
#
# このファイルは何？
# - 候補抽出結果を「保存済みスナップショット」として持つモデルです。
# - 重い候補抽出を画面表示時に毎回やらず、
#   先に計算して保存 → 画面は読むだけ、にするために使います。
# =========================================================

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils import timezone


class CandidateSnapshot(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="tradeai_candidate_snapshots",
    )

    built_at = models.DateTimeField("作成日時", default=timezone.now, db_index=True)

    score_max = models.PositiveIntegerField("総合点の最大値", default=100)

    universe_count = models.PositiveIntegerField("監視ユニバース数", default=0)
    candidate_total = models.PositiveIntegerField("候補合計", default=0)
    long_candidate_count = models.PositiveIntegerField("ロング候補数", default=0)
    short_candidate_count = models.PositiveIntegerField("ショート候補数", default=0)
    strong_candidate_count = models.PositiveIntegerField("強い候補数", default=0)
    attention_candidate_count = models.PositiveIntegerField("注目候補数", default=0)

    universe_source_counts = models.JSONField("ユニバース内訳", default=dict, blank=True)
    candidate_source_counts = models.JSONField("候補の由来内訳", default=dict, blank=True)

    last_regime = models.JSONField("地合いスナップショット", default=dict, blank=True)
    debug_stats = models.JSONField("デバッグ統計", default=dict, blank=True)

    long_rows = models.JSONField("ロング候補表示行", default=list, blank=True)
    short_rows = models.JSONField("ショート候補表示行", default=list, blank=True)

    created_at = models.DateTimeField("レコード作成日時", auto_now_add=True)

    class Meta:
        ordering = ["-built_at", "-id"]
        indexes = [
            models.Index(fields=["user", "built_at"]),
        ]

    def __str__(self):
        return f"{self.user} / CandidateSnapshot / {self.built_at:%Y-%m-%d %H:%M}"