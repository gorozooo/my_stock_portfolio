# =========================================
# [FILE] category.py
# [PATH] kakeibo/models/category.py
#
# このファイルは何？
# 家計簿の「分類（カテゴリ）」をDBで管理するモデル。
# - 収入カテゴリ / 支出カテゴリ を分離
# - code は「集計や判定のための固定ID」
#   例）支出の CARD / ADVANCE など
#
# ★重要（今回の修正）
# これまで unique_together(type, code) があるせいで、
# codeが空("")のカテゴリを2つ目以降作るとDBが重複扱いで落ちていた。
# → codeが空じゃない時だけユニーク制約をかける（Partial Unique）。
# =========================================

from django.db import models
from django.db.models import Q


class Category(models.Model):
    TYPE_CHOICES = [
        ("EXPENSE", "支出"),
        ("INCOME", "収入"),
    ]

    type = models.CharField("種類", max_length=20, choices=TYPE_CHOICES)

    # ✅ 集計用の固定コード（例：CARD / ADVANCE など）
    # ふつうのカテゴリは空でOK（空は無制限に許可する）
    code = models.CharField("コード", max_length=30, blank=True, default="")

    name = models.CharField("分類名", max_length=50)
    order = models.IntegerField("表示順", default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["order", "id"]

        # ✅ 「同じ種類で同じ名前」は禁止（ここは従来どおり）
        unique_together = (
            ("type", "name"),
        )

        # ✅ codeは「空じゃない時だけ」ユニークにする（これが今回の本丸）
        constraints = [
            models.UniqueConstraint(
                fields=["type", "code"],
                condition=~Q(code=""),
                name="uniq_kakeibo_category_type_code_nonempty",
            ),
        ]

    def __str__(self):
        # ✅ UIはとにかくシンプル
        return self.name