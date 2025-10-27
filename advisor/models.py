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

class TrendResult(models.Model):
    """
    作戦ボードの根拠データ（銘柄ごとの日次/週次トレンドなど）
    - asof: 計算対象日（1銘柄1日1レコード想定）
    - win_prob: その銘柄のAI勝率（0-1）
    - weekly_trend: 'up' / 'flat' / 'down'
    - overall_score: 0-100（ win_prob×0.7 + theme_score×0.3 のような合成点を格納しておく）
    - theme: テーマ名とスコア（0-1）
    - entry_price_hint: IN目安（終値など）
    """
    TREND_CHOICES = (
        ("up", "上向き"),
        ("flat", "横ばい"),
        ("down", "下向き"),
    )

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, db_index=True)
    ticker = models.CharField(max_length=16, db_index=True)
    name   = models.CharField(max_length=128, blank=True, default="")
    asof   = models.DateField(db_index=True)

    close_price      = models.IntegerField(null=True, blank=True)   # 参考終値
    entry_price_hint = models.IntegerField(null=True, blank=True)   # IN目安（=closeでもOK）

    weekly_trend   = models.CharField(max_length=8, choices=TREND_CHOICES, default="flat")
    win_prob       = models.FloatField(null=True, blank=True)       # 0.0 - 1.0
    theme_label    = models.CharField(max_length=64, blank=True, default="")
    theme_score    = models.FloatField(null=True, blank=True)       # 0.0 - 1.0
    overall_score  = models.IntegerField(null=True, blank=True)     # 0 - 100

    # 将来拡張（安全にnull許容）
    size_mult      = models.FloatField(null=True, blank=True)       # 強いとき>1.0
    notes          = models.JSONField(default=dict, blank=True)     # 任意の根拠

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "ticker", "asof"], name="uniq_trend_user_ticker_asof"),
        ]
        indexes = [
            models.Index(fields=["user", "asof", "overall_score"]),
            models.Index(fields=["user", "weekly_trend"]),
        ]
        ordering = ["-asof", "-overall_score", "-updated_at"]

    def __str__(self):
        return f"{self.asof} {self.ticker} {self.overall_score}"
