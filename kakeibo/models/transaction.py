# =========================================
# [FILE] transaction.py
# [PATH] kakeibo/models/transaction.py
#
# このファイルは何？
# 家計簿の取引データを保存するモデル。
# v1では「カードまとめ」「固定費」「立替」など“家計簿向けの最小ラベル”を追加する。
# 明細（スーパーの買い物単位）までは入れず、月次まとめ入力を基本にする。
# =========================================

from django.db import models
from .account import Account


class Transaction(models.Model):

    TYPE_CHOICES = [
        ("EXPENSE", "支出"),
        ("INCOME", "収入"),
        ("TRANSFER", "振替"),
    ]

    # 家計簿用：入力をシンプルにするための分類（将来拡張しやすい最小セット）
    CATEGORY_CHOICES = [
        ("CARD_SUMMARY", "カードまとめ"),
        ("FIXED", "固定費"),
        ("ADVANCE", "立替"),
        ("OTHER", "その他"),
    ]

    # 家計簿用：誰が払ったか（個人残高は表示しない。立替の識別だけ）
    PAID_BY_CHOICES = [
        ("HOUSEHOLD", "家計"),
        ("HUSBAND", "夫"),
        ("WIFE", "妻"),
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

    category = models.CharField(
        "分類",
        max_length=20,
        choices=CATEGORY_CHOICES,
        default="CARD_SUMMARY"
    )

    paid_by = models.CharField(
        "支払者",
        max_length=20,
        choices=PAID_BY_CHOICES,
        default="HOUSEHOLD"
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
        return f"{self.date} {self.type} {self.amount}"