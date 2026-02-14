# =========================================
# [FILE] category.py
# [PATH] kakeibo/models/category.py
#
# このファイルは何？
# 家計簿の「分類（カテゴリ）」をDBで管理するモデル。
# - 収入カテゴリ / 支出カテゴリ を分離して持つ
# - 設定画面から追加・編集・削除できる前提
# - code は「集計や判定のための固定ID」(表示名を変えても壊れない)
# =========================================

from django.db import models


class Category(models.Model):
    TYPE_CHOICES = [
        ("EXPENSE", "支出"),
        ("INCOME", "収入"),
    ]

    type = models.CharField("種類", max_length=20, choices=TYPE_CHOICES)

    # ✅ 集計用の固定コード（例：CARD_SUMMARY / ADVANCE）
    # ふつうのカテゴリは空でもOK（設定で作るカテゴリは空のままでOK）
    code = models.CharField("コード", max_length=30, blank=True, default="")

    name = models.CharField("分類名", max_length=50)
    order = models.IntegerField("表示順", default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["order", "id"]
        unique_together = (
            ("type", "name"),
            ("type", "code"),
        )

    def __str__(self):
        if self.code:
            return f"{self.get_type_display()}:{self.name} ({self.code})"
        return f"{self.get_type_display()}:{self.name}"