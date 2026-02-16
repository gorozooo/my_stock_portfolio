# =========================================
# [FILE] account.py
# [PATH] kakeibo/models/account.py
#
# このファイルは何？
# 家計簿の「口座/カード」を管理するモデル。
# - kindで「口座」と「カード」を分離（設定タブを分けるため）
# - ownerで「誰のものか(B/G/家計)」を持つ（銀行タブでB/G枠を作るため）
# =========================================

from django.db import models


class Account(models.Model):
    KIND_CHOICES = [
        ("ACCOUNT", "口座"),
        ("CARD", "カード"),
    ]

    OWNER_CHOICES = [
        ("HOUSE", "家"),
        ("B", "ぼーや"),
        ("G", "ゴロ"),
    ]

    kind = models.CharField(
        "種類",
        max_length=20,
        choices=KIND_CHOICES,
        default="ACCOUNT",
    )

    owner = models.CharField(
        "所有者",
        max_length=10,
        choices=OWNER_CHOICES,
        default="HOUSE",
    )

    name = models.CharField("名前", max_length=100)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["kind", "owner", "id"]

    def __str__(self):
        # ✅ 表示はシンプル（ドロップダウンでゴチャつかせない）
        return self.name