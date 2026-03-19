# =========================================
# [FILE] monthly_dashboard_memo.py
# [PATH] kakeibo/models/monthly_dashboard_memo.py
#
# このファイルは何？
# - 家計簿ダッシュボード下部に表示する「月ごとの自由メモ」を保存するモデルです。
# - 1ヶ月につき1件だけ保存します。
# - month は必ずその月の1日（YYYY-MM-01）に揃えて保存します。
#
# 使い方イメージ：
# - 2026-03 を表示中なら、その月のメモを表示・保存
# - 2026-04 に切り替えたら、その月のメモを表示・保存
# =========================================

from datetime import date

from django.db import models


class MonthlyDashboardMemo(models.Model):
    month = models.DateField("対象月", unique=True)
    memo = models.TextField("メモ", blank=True, default="")

    created_at = models.DateTimeField("作成日時", auto_now_add=True)
    updated_at = models.DateTimeField("更新日時", auto_now=True)

    class Meta:
        ordering = ["-month", "-id"]
        verbose_name = "月次ダッシュボードメモ"
        verbose_name_plural = "月次ダッシュボードメモ"

    def save(self, *args, **kwargs):
        """
        何をする？
        - month を必ず YYYY-MM-01 に正規化して保存する
        """
        if self.month:
            self.month = date(self.month.year, self.month.month, 1)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.month:%Y-%m} メモ"