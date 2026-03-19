# =========================================
# [FILE] dashboard_month_note.py
# [PATH] kakeibo/models/dashboard_month_note.py
#
# このファイルは何？
# - 家計簿トップ画面（/kakeibo/）の「月メモ」を保存するためのモデルです。
# - 月ごとに1件だけメモを持てるようにします。
# - dashboard の「選択中の月」に連動して、表示・保存する想定です。
#
# 仕様
# - month : 対象月（内部では必ず1日固定で扱う）
# - memo  : その月の自由メモ
# - created_at / updated_at : 作成日時 / 更新日時
# =========================================

from django.db import models


class DashboardMonthNote(models.Model):
    month = models.DateField("対象月", unique=True)
    memo = models.TextField("メモ", blank=True, default="")
    created_at = models.DateTimeField("作成日時", auto_now_add=True)
    updated_at = models.DateTimeField("更新日時", auto_now=True)

    class Meta:
        ordering = ["-month", "-updated_at"]
        verbose_name = "月メモ"
        verbose_name_plural = "月メモ"

    def __str__(self):
        return f"{self.month} 月メモ"