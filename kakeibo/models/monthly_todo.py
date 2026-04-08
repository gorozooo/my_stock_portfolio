# =========================================
# [FILE] monthly_todo.py
# [PATH] kakeibo/models/monthly_todo.py
#
# このファイルは何？
# - ダッシュボードの「月ごとのTODOリスト」を保存するモデル。
# - month に紐づいて、その月専用のTODOを管理する。
# - status で「未着手 / 進行中 / 完了」を持つ。
# - sort_order で並び順を調整できる。
# - 将来的にスマホで並び替えや完了切替をしやすくするための土台。
# =========================================

from django.db import models


class MonthlyTodo(models.Model):
    STATUS_TODO = "TODO"
    STATUS_DOING = "DOING"
    STATUS_DONE = "DONE"

    STATUS_CHOICES = [
        (STATUS_TODO, "未着手"),
        (STATUS_DOING, "進行中"),
        (STATUS_DONE, "完了"),
    ]

    month = models.DateField("対象月")
    title = models.CharField("やること", max_length=200)
    note = models.TextField("メモ", blank=True)

    status = models.CharField(
        "状態",
        max_length=10,
        choices=STATUS_CHOICES,
        default=STATUS_TODO,
    )

    sort_order = models.PositiveIntegerField("並び順", default=0)

    completed_at = models.DateTimeField("完了日時", null=True, blank=True)
    created_at = models.DateTimeField("作成日時", auto_now_add=True)
    updated_at = models.DateTimeField("更新日時", auto_now=True)

    class Meta:
        ordering = ["month", "sort_order", "id"]
        verbose_name = "月TODO"
        verbose_name_plural = "月TODO"

    def __str__(self):
        month_label = self.month.strftime("%Y-%m") if self.month else "----"
        return f"{month_label} [{self.get_status_display()}] {self.title}"