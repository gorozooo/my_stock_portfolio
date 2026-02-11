# =========================================
# [FILE] transaction.py
# [PATH] kakeibo/models/transaction.py
#
# このファイルは何？
# 家計簿の取引データを保存するモデル。
# 支出・収入・振替を管理する。
# =========================================

from django.db import models
from .account import Account


class Transaction(models.Model):

    TYPE_CHOICES = [
        ("EXPENSE", "支出"),
        ("INCOME", "収入"),
        ("TRANSFER", "振替"),
    ]

    type = models.CharField(
        "取引タイプ",
        max_length=20,
        choices=TYPE_CHOICES
    )

    account = models.ForeignKey(
        Account,
        on_delete=models.CASCADE,
        related_name="transactions"
    )

    amount = models.DecimalField(
        "金額",
        max_digits=12,
        decimal_places=0
    )

    memo = models.CharField(
        "メモ",
        max_length=255,
        blank=True
    )

    date = models.DateField("日付")

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.date} {self.amount}"