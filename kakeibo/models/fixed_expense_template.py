# =========================================
# [FILE] fixed_expense_template.py
# [PATH] kakeibo/models/fixed_expense_template.py
#
# このファイルは何？
# 固定費のテンプレ。
# - 一度登録したら、変更しない限り毎月同じ支出として扱う
# - month を持たない（毎月自動適用）
# =========================================

from django.db import models
from .category import Category


class FixedExpenseTemplate(models.Model):
    OWNER_CHOICES = [
        ("HOUSE", "家"),
        ("B", "ぼーや"),
        ("G", "ごろ"),
    ]

    owner = models.CharField("誰の支出", max_length=10, choices=OWNER_CHOICES)

    category = models.ForeignKey(
        Category,
        on_delete=models.PROTECT,
        related_name="fixed_expense_templates",
        verbose_name="分類",
        limit_choices_to={"type": "EXPENSE"},
    )

    amount = models.DecimalField("金額", max_digits=12, decimal_places=0)
    memo = models.CharField("メモ", max_length=255, blank=True)
    is_active = models.BooleanField("有効", default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-is_active", "id"]

    def __str__(self):
        return f"{self.owner} FIXED {self.amount}"