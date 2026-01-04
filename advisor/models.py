from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils import timezone

User = settings.AUTH_USER_MODEL


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    updated_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        # 明示的に updated_at を更新（auto_now 相当だが、手動更新にも対応）
        self.updated_at = timezone.now()
        return super().save(*args, **kwargs)


class ActionLog(TimeStampedModel):
    user       = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL)
    ticker     = models.CharField(max_length=20, db_index=True)
    policy_id  = models.CharField(max_length=64, blank=True, default="")
    action     = models.CharField(max_length=32, blank=True, default="")   # e.g. save_order / reject
    note       = models.TextField(blank=True, default="")

    def __str__(self):
        return f"{self.ticker} {self.action} ({self.created_at:%Y-%m-%d})"


class Reminder(TimeStampedModel):
    user    = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL)
    ticker  = models.CharField(max_length=20, db_index=True)
    message = models.CharField(max_length=140, blank=True, default="")
    fire_at = models.DateTimeField(db_index=True)

    def __str__(self):
        return f"⏰ {self.ticker} {self.fire_at:%Y-%m-%d %H:%M}"


class WatchEntry(TimeStampedModel):
    STATUS_ACTIVE   = "active"
    STATUS_ARCHIVED = "archived"
    STATUS_CHOICES = (
        (STATUS_ACTIVE, "Active"),
        (STATUS_ARCHIVED, "Archived"),
    )

    user    = models.ForeignKey(User, on_delete=models.CASCADE, related_name="watch_entries")
    ticker  = models.CharField(max_length=20, db_index=True)
    name    = models.CharField(max_length=120, blank=True, default="")
    status  = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_ACTIVE, db_index=True)

    # ユーザーの任意メモ
    note    = models.TextField(blank=True, default="")

    # --- ボードからコピーした説明（サマリ／詳細） ---
    reason_summary = models.TextField(blank=True, default="")
    # JSON: ["半導体テーマが強い（78点）", "出来高が増えている（+35%）", ...]
    # ※ フロントは配列前提なので null ではなく空配列を既定にする
    reason_details = models.JSONField(blank=True, default=list)

    # テーマ／AIのメタ
    theme_label = models.CharField(max_length=60, blank=True, default="")
    theme_score = models.FloatField(blank=True, null=True)   # 0-1
    ai_win_prob = models.FloatField(blank=True, null=True)   # 0-1

    # 目標・損切（テキスト互換）
    target_tp = models.CharField(max_length=120, blank=True, default="")
    target_sl = models.CharField(max_length=120, blank=True, default="")

    # === 将来利用の拡張フィールド ===
    overall_score      = models.IntegerField(blank=True, null=True)   # 0-100
    weekly_trend       = models.CharField(max_length=8, blank=True, default="")  # "up"|"flat"|"down"

    entry_price_hint   = models.IntegerField(blank=True, null=True)   # IN 目安（円）
    tp_price           = models.IntegerField(blank=True, null=True)   # 目標価格（円）
    sl_price           = models.IntegerField(blank=True, null=True)   # 損切価格（円）
    tp_pct             = models.FloatField(blank=True, null=True)     # 0-1
    sl_pct             = models.FloatField(blank=True, null=True)     # 0-1

    position_size_hint = models.IntegerField(blank=True, null=True)   # 数量目安（株数）
    in_position        = models.BooleanField(default=False)

    class Meta:
        # ※ 過去の unique_together(user, ticker, status) は衝突源だったため設定しない
        indexes = [
            models.Index(fields=["user", "status", "updated_at"]),
            models.Index(fields=["user", "ticker"]),
        ]
        ordering = ["-updated_at", "-id"]

    def __str__(self):
        return f"[{self.status}] {self.ticker} {self.name}"

    # UI向けに「いつ入れたか」を明示名で取りたい時用
    @property
    def added_at(self):
        return self.created_at


class Policy(TimeStampedModel):
    """
    売買ポリシー（ユーザー1人に1レコード想定）
    - risk_mode : 攻め / 普通 / 守り / おまかせ
    - hold_style: 短期 / 中期 / 長期 / おまかせ
    """
    MODE_ATTACK  = "attack"
    MODE_NORMAL  = "normal"
    MODE_DEFENSE = "defense"
    MODE_AUTO    = "auto"

    STYLE_SHORT = "short"
    STYLE_MID   = "mid"
    STYLE_LONG  = "long"
    STYLE_AUTO  = "auto"

    MODE_CHOICES = (
        (MODE_ATTACK,  "攻め"),
        (MODE_NORMAL,  "普通"),
        (MODE_DEFENSE, "守り"),
        (MODE_AUTO,    "おまかせ"),
    )
    STYLE_CHOICES = (
        (STYLE_SHORT, "短期"),
        (STYLE_MID,   "中期"),
        (STYLE_LONG,  "長期"),
        (STYLE_AUTO,  "おまかせ"),
    )

    user       = models.ForeignKey(User, on_delete=models.CASCADE, related_name="policies")
    risk_mode  = models.CharField(max_length=16, choices=MODE_CHOICES, default=MODE_NORMAL)
    hold_style = models.CharField(max_length=16, choices=STYLE_CHOICES, default=STYLE_MID)

    class Meta:
        indexes  = [models.Index(fields=["user"])]
        unique_together = (("user",),)  # ユーザー1件想定（将来バージョン違いに拡張するなら外す）
        verbose_name = "Policy"
        verbose_name_plural = "Policies"

    def __str__(self):
        return f"{self.user_id}: {self.get_risk_mode_display()} × {self.get_hold_style_display()}"


# ===== ここがポイント：TrendResult は models_trend 側で定義し、このファイルでは再エクスポートだけ =====
from .models_trend import TrendResult  # noqa: F401

try:
    # 別ファイルのモデルを読み込ませて、makemigrations の対象にする
    from .models_indicator import IndicatorSnapshot  # noqa: F401
except Exception:
    # ローカル作業中にファイル未保存でも落ちないように
    pass
