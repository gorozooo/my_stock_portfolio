# =========================================================
# [FILE] learning_result.py
# [PATH] <project_root>/tradeai/models/learning_result.py
#
# このファイルは何？
# - LearningSnapshot に対する最終結果を保存するモデルです。
# - 勝敗、損益、保有日数、手仕舞い理由を持ちます。
# =========================================================

from django.db import models
from django.utils import timezone


class LearningResult(models.Model):
    class ResultLabelChoices(models.TextChoices):
        WIN = "WIN", "勝ち"
        LOSS = "LOSS", "負け"
        FLAT = "FLAT", "引き分け"
        UNKNOWN = "UNKNOWN", "未判定"

    snapshot = models.OneToOneField(
        "tradeai.LearningSnapshot",
        on_delete=models.CASCADE,
        related_name="result",
    )

    settled_at = models.DateTimeField("確定日時", default=timezone.now, db_index=True)
    result_label = models.CharField(
        "結果",
        max_length=16,
        choices=ResultLabelChoices.choices,
        default=ResultLabelChoices.UNKNOWN,
    )

    hold_days = models.PositiveIntegerField("保有日数", null=True, blank=True)
    pnl_yen = models.DecimalField("損益額", max_digits=14, decimal_places=2, null=True, blank=True)
    pnl_pct = models.DecimalField("損益率", max_digits=8, decimal_places=2, null=True, blank=True)
    max_favorable_pct = models.DecimalField("最大有利率", max_digits=8, decimal_places=2, null=True, blank=True)
    max_adverse_pct = models.DecimalField("最大不利率", max_digits=8, decimal_places=2, null=True, blank=True)

    exit_reason = models.TextField("終了理由", blank=True, default="")
    result_payload = models.JSONField("結果詳細", default=dict, blank=True)

    created_at = models.DateTimeField("作成日時", auto_now_add=True)

    class Meta:
        ordering = ["-settled_at", "-id"]
        indexes = [
            models.Index(fields=["settled_at"]),
            models.Index(fields=["result_label"]),
        ]

    def __str__(self):
        return f"{self.snapshot.ticker} {self.result_label}"