# =========================================
# [FILE] bank_balance.py
# [PATH] kakeibo/models/bank_balance.py
#
# このファイルは何？
# 銀行残高（口座残高）を月次で手入力するモデル。
# - 口座（Account.kind=ACCOUNT）は設定タブの「口座」で追加
# - owner は Account.owner を使う想定（B/Gで枠を出す）
# =========================================

from django.db import models
from .account import Account


class BankBalance(models.Model):
    month = models.DateField("対象月")  # 常に day=1 に揃える

    account = models.ForeignKey(
        Account,
        on_delete=models.CASCADE,
        related_name="bank_balances",
        verbose_name="口座",
        limit_choices_to={"kind": "ACCOUNT"},
    )

    balance = models.DecimalField("残高", max_digits=12, decimal_places=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-month", "account__id"]
        unique_together = ("month", "account")

    def __str__(self):
        return f"{self.month} {self.account} {self.balance}"