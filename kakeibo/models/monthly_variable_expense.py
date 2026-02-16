# =========================================
# [FILE] monthly_variable_expense.py
# [PATH] kakeibo/models/monthly_variable_expense.py
#
# このファイルは何？
# 変動費を「月次」で保存するモデル。
# - month は月単位（内部はDateFieldで必ず1日固定）
# - var_type は「種類」：CARD / ADVANCE / PENSION / OTHER
# - CARD の場合だけ card を選ぶ（設定で登録したカード）
# - category は「支出カテゴリ」から自動で決める（code = var_type）
#   → 画面で分類入力は不要（あなたの方針どおり）
# =========================================

from django.db import models
from .account import Account
from .category import Category


class MonthlyVariableExpense(models.Model):
    OWNER_CHOICES = [
        ("HOUSE", "家"),
        ("B", "ぼーや"),
        ("G", "ごろ"),
    ]

    # ✅ 種類に「年金・保険」「その他」を追加
    VAR_TYPE_CHOICES = [
        ("CARD", "カード"),
        ("ADVANCE", "立替"),
        ("PENSION", "年金・保険"),
        ("OTHER", "その他"),
    ]

    month = models.DateField("対象月")  # 常に day=1 に揃える
    owner = models.CharField("誰の支出", max_length=10, choices=OWNER_CHOICES)

    var_type = models.CharField("種類", max_length=20, choices=VAR_TYPE_CHOICES)

    # ✅ 分類は Category で保持する（ただし入力はさせない：フォームで自動決定）
    #    ルール：Category(type=EXPENSE, code=var_type) をセットする
    category = models.ForeignKey(
        Category,
        on_delete=models.PROTECT,
        related_name="monthly_variable_expenses",
        verbose_name="分類",
        limit_choices_to={"type": "EXPENSE"},
    )

    # CARD のときだけ使う（それ以外は空）
    card = models.ForeignKey(
        Account,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="monthly_card_expenses",
        verbose_name="カード",
        limit_choices_to={"kind": "CARD"},
    )

    amount = models.DecimalField("金額", max_digits=12, decimal_places=0)
    memo = models.CharField("メモ", max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-month", "-id"]

    def __str__(self):
        return f"{self.month} {self.owner} {self.var_type} -{self.amount}"