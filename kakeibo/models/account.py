# =========================================
# [FILE] account.py
# [PATH] kakeibo/models/account.py
#
# このファイルは何？
# 家計簿の「口座」を管理するモデル。
# 銀行・現金・カード・証券などを登録する。
# =========================================

from django.db import models


class Account(models.Model):
    name = models.CharField("口座名", max_length=100)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name