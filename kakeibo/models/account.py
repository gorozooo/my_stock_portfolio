# =========================================
# [FILE] account.py
# [PATH] kakeibo/models/account.py
#
# このファイルは何？
# 家計簿の「口座/カード」を管理するモデル。
# - kind により「口座」と「カード」を分けて扱える
# - 設定画面のタブ（口座 / カード）で管理する
# =========================================

from django.db import models


class Account(models.Model):
    KIND_CHOICES = [
        ("ACCOUNT", "口座"),
        ("CARD", "カード"),
    ]

    kind = models.CharField("種類", max_length=20, choices=KIND_CHOICES, default="ACCOUNT")
    name = models.CharField("名前", max_length=100)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["kind", "id"]
        unique_together = ("kind", "name")

    def __str__(self):
        return f"{self.get_kind_display()}:{self.name}"