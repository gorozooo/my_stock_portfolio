# =========================================
# [FILE] transaction.py
# [PATH] kakeibo/models/transaction.py
#
# このファイルは何？
# 家計簿の取引データを保存するモデル。
# - category は DB管理の Category 参照（増やせる）
# - paid_by は「立替識別」用途（夫/妻/家計）
# =========================================

from django.db import models
from .account import Account
from .category import Category


class Transaction(models.Model):
    TYPE_CHOICES = [
        ("EXPENSE", "支出"),
        ("INCOME", "収入"),
        ("TRANSFER", "振替"),
    ]

    PAID_BY_CHOICES = [
        ("HOUSEHOLD", "家計"),
        ("HUSBAND", "夫"),
        ("WIFE", "妻"),
    ]

    type = models.CharField("取引タイプ", max_length=20, choices=TYPE_CHOICES)

    account = models.ForeignKey(
        Account,
        on_delete=models.CASCADE,
        related_name="transactions",
        verbose_name="Account",
    )

    category = models.ForeignKey(
        Category,
        on_delete=models.PROTECT,
        related_name="transactions",
        verbose_name="分類",
    )

    paid_by = models.CharField(
        "支払者",
        max_length=20,
        choices=PAID_BY_CHOICES,
        default="HOUSEHOLD",
    )

    amount = models.DecimalField("金額", max_digits=12, decimal_places=0)
    memo = models.CharField("メモ", max_length=255, blank=True)
    date = models.DateField("日付")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.date} {self.type} {self.amount}"