# =========================================
# [FILE] monthly_snapshot.py
# [PATH] kakeibo/models/monthly_snapshot.py
#
# このファイルは何？
# 月次の「確定スナップショット」を保存するモデル。
# - 画面の「この月を確定」ボタンで作成/上書き（month=YYYY-MM-01でユニーク）
# - 確定時点の収支、固定費（テンプレ合計）、銀行残高、投資、総資産などを保存し
#   “その月の正”として再計算ブレを防ぐ。
# =========================================

from django.db import models
from django.utils import timezone


class MonthlySnapshot(models.Model):
    # 対象月（必ず day=1 に揃える）
    month = models.DateField(unique=True, db_index=True)

    # 収支（確定値）
    income = models.IntegerField(default=0)
    fixed = models.IntegerField(default=0)
    variable = models.IntegerField(default=0)
    expense_total = models.IntegerField(default=0)
    diff = models.IntegerField(default=0)

    # KPI（確定値）
    kpi_total_assets = models.IntegerField(default=0)         # 我が家の総資産
    kpi_rakuten_bank_actual = models.IntegerField(default=0)  # 楽天銀行残高（あなたの定義）
    kpi_invest_total = models.IntegerField(default=0)         # 投資（評価額+余力）

    # 内訳（確定値）
    rakuten_eval = models.IntegerField(default=0)
    rakuten_cash_free = models.IntegerField(default=0)
    rakuten_bank_b = models.IntegerField(default=0)
    aeon_bank_house = models.IntegerField(default=0)

    # メタ（いつ確定したか）
    locked_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-month"]

    def __str__(self):
        return f"MonthlySnapshot({self.month})"