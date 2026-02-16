# =========================================
# [FILE] monthly_income.py
# [PATH] kakeibo/models/monthly_income.py
#
# このファイルは何？
# 月次の収入を保存するモデル。
# - 収入は「どこに入ったか（口座/カード）」不要：あなたの仕様どおり
# - month は月単位（内部はDateFieldで必ず1日固定）
# =========================================

from django.db import models
from .category import Category


class MonthlyIncome(models.Model):
    OWNER_CHOICES = [
        ("HOUSE", "家"),
        ("B", "ぼーや"),
        ("G", "ごろ"),
    ]

    month = models.DateField("対象月")  # 常に day=1 に揃える
    owner = models.CharField("誰の収入", max_length=10, choices=OWNER_CHOICES)

    category = models.ForeignKey(
        Category,
        on_delete=models.PROTECT,
        related_name="monthly_incomes",
        verbose_name="分類",
        limit_choices_to={"type": "INCOME"},
    )

    amount = models.DecimalField("金額", max_digits=12, decimal_places=0)
    memo = models.CharField("メモ", max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-month", "-id"]
        unique_together = ("month", "owner", "category", "memo")

    def __str__(self):
        return f"{self.month} {self.owner} +{self.amount}"