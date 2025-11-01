from __future__ import annotations
from django.db import models
from django.contrib.auth import get_user_model

User = get_user_model()

class OrderMemo(models.Model):
    """
    LINEカードで「発注メモに保存」した時点のスナップショットを保存。
    将来の発注フォーム連携や履歴検索の土台になる。
    """
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="order_memos")

    # 銘柄情報（見出しで使った和名を固定保存）
    ticker = models.CharField(max_length=16, db_index=True)
    name = models.CharField(max_length=128, blank=True, default="")  # 例: トヨタ自動車

    # 通知ウィンドウ（preopen/intraday/close など）
    window = models.CharField(max_length=32, blank=True, default="")

    # 採用ポリシー名（表示用に join した文字列）
    policies_line = models.CharField(max_length=256, blank=True, default="")

    # 価格系（整数円で統一・None許容）
    entry_price = models.IntegerField(null=True, blank=True)
    tp_price = models.IntegerField(null=True, blank=True)
    sl_price = models.IntegerField(null=True, blank=True)

    # 参考スコア等（表示値を残す）
    score = models.IntegerField(null=True, blank=True)
    weekly_trend = models.CharField(max_length=16, blank=True, default="")
    slope_yr = models.FloatField(null=True, blank=True)    # 0.785 → 78.5%/yr
    theme = models.FloatField(null=True, blank=True)       # 0.55 → 55

    # 元データのJSON（将来の再現・監査用に残す）
    policy_snapshot = models.JSONField(blank=True, null=True)
    trend_snapshot = models.JSONField(blank=True, null=True)
    meta = models.JSONField(blank=True, null=True)

    # どこから保存したか
    source = models.CharField(max_length=32, default="line", blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.created_at:%Y-%m-%d %H:%M} {self.ticker} {self.name}"