"""
[FILE] models.py
[PATH] <project_root>/shihyo/models.py

このファイルは何？
- 指標専用アプリ shihyo のDBモデルです。
- 既存の
  1) MarketIndicatorSnapshot
  2) ShihyoDailyPrice
  3) ShihyoMarketBiasSnapshot
  に加えて、
  4) 場中価格保存用の ShihyoIntradayPrice
  を追加します。
- 将来的に 10:00確認(open_1000) の実績集計は、このモデルを土台に作ります。
"""

from django.db import models


class MarketIndicatorSnapshot(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)

    # --- raw values ---
    nikkei_futures_last = models.FloatField(null=True, blank=True)
    nikkei_futures_change = models.FloatField(null=True, blank=True)
    nikkei_futures_change_pct = models.FloatField(null=True, blank=True)

    usdjpy_last = models.FloatField(null=True, blank=True)
    usdjpy_change = models.FloatField(null=True, blank=True)
    usdjpy_change_pct = models.FloatField(null=True, blank=True)

    vix_last = models.FloatField(null=True, blank=True)
    vix_change = models.FloatField(null=True, blank=True)
    vix_change_pct = models.FloatField(null=True, blank=True)

    # --- beginner-friendly labels ---
    nikkei_label = models.CharField(max_length=32, default="", blank=True)
    fx_label = models.CharField(max_length=32, default="", blank=True)
    vix_label = models.CharField(max_length=32, default="", blank=True)

    # --- final action (the important part) ---
    action_title = models.CharField(max_length=64, default="", blank=True)
    action_lines = models.JSONField(default=list, blank=True)

    # --- debug / source ---
    source = models.CharField(max_length=32, default="stooq")
    raw_payload = models.JSONField(default=dict, blank=True)
    error = models.TextField(default="", blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Snapshot {self.created_at:%Y-%m-%d %H:%M}"


class ShihyoDailyPrice(models.Model):
    """
    指標専用の日次価格保存テーブル。
    - code / name は aiapp.StockMaster と突合する前提
    - sector_name は集計を軽くするため保存時点で持っておく
    - まずは日次ベースの市場の偏り用
    """
    date = models.DateField(db_index=True)

    code = models.CharField(max_length=12, db_index=True)
    name = models.CharField(max_length=255, blank=True, default="")

    sector_code = models.CharField(max_length=16, null=True, blank=True, db_index=True)
    sector_name = models.CharField(max_length=255, null=True, blank=True, db_index=True)

    close = models.FloatField(null=True, blank=True)
    prev_close = models.FloatField(null=True, blank=True)
    change = models.FloatField(null=True, blank=True)
    change_pct = models.FloatField(null=True, blank=True)

    volume = models.BigIntegerField(null=True, blank=True)
    turnover = models.FloatField(null=True, blank=True)

    source = models.CharField(max_length=32, default="manual", blank=True)
    raw_payload = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-date", "code"]
        unique_together = ("date", "code")
        indexes = [
            models.Index(fields=["date", "code"]),
            models.Index(fields=["date", "sector_name"]),
            models.Index(fields=["sector_name", "date"]),
        ]

    def __str__(self) -> str:
        return f"{self.date} {self.code} {self.name}"


class ShihyoIntradayPrice(models.Model):
    """
    指標専用の場中価格保存テーブル。
    - 10:00確認(open_1000) 用の実績データを保存する
    - 将来、他の時刻スロットにも拡張できるよう mode を持つ
    """
    MODE_OPEN_1000 = "open_1000"

    MODE_CHOICES = [
        (MODE_OPEN_1000, "10時確認"),
    ]

    date = models.DateField(db_index=True)
    mode = models.CharField(max_length=16, choices=MODE_CHOICES, db_index=True)

    captured_at = models.DateTimeField(db_index=True)

    code = models.CharField(max_length=12, db_index=True)
    name = models.CharField(max_length=255, blank=True, default="")

    sector_code = models.CharField(max_length=16, null=True, blank=True, db_index=True)
    sector_name = models.CharField(max_length=255, null=True, blank=True, db_index=True)

    last = models.FloatField(null=True, blank=True)
    prev_close = models.FloatField(null=True, blank=True)
    change = models.FloatField(null=True, blank=True)
    change_pct = models.FloatField(null=True, blank=True)

    volume = models.BigIntegerField(null=True, blank=True)
    turnover = models.FloatField(null=True, blank=True)

    source = models.CharField(max_length=32, default="manual", blank=True)
    raw_payload = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-date", "mode", "code"]
        unique_together = ("date", "mode", "code")
        indexes = [
            models.Index(fields=["date", "mode", "code"]),
            models.Index(fields=["date", "mode", "sector_name"]),
            models.Index(fields=["mode", "captured_at"]),
            models.Index(fields=["sector_name", "date", "mode"]),
        ]

    def __str__(self) -> str:
        return f"{self.date} {self.mode} {self.code} {self.name}"


class ShihyoMarketBiasSnapshot(models.Model):
    """
    市場の偏りの完成データ。
    mode:
      - close     : 引け後の実績
      - preopen   : 朝7:00予報
      - open_1000 : 10:00確認
    """
    MODE_CLOSE = "close"
    MODE_PREOPEN = "preopen"
    MODE_OPEN_1000 = "open_1000"

    MODE_CHOICES = [
        (MODE_CLOSE, "引け後"),
        (MODE_PREOPEN, "寄り前"),
        (MODE_OPEN_1000, "10時確認"),
    ]

    TONE_RISK_ON = "risk_on"
    TONE_NEUTRAL = "neutral"
    TONE_RISK_OFF = "risk_off"

    TONE_CHOICES = [
        (TONE_RISK_ON, "やや強い"),
        (TONE_NEUTRAL, "まちまち"),
        (TONE_RISK_OFF, "やや弱い"),
    ]

    date = models.DateField(db_index=True)
    mode = models.CharField(max_length=16, choices=MODE_CHOICES, db_index=True)

    summary_title = models.CharField(max_length=64, default="", blank=True)
    summary_text = models.TextField(default="", blank=True)
    tone = models.CharField(
        max_length=16,
        choices=TONE_CHOICES,
        default=TONE_NEUTRAL,
        db_index=True,
    )

    strong_sectors = models.JSONField(default=list, blank=True)
    weak_sectors = models.JSONField(default=list, blank=True)
    hot_themes = models.JSONField(default=list, blank=True)
    cold_themes = models.JSONField(default=list, blank=True)

    raw_detail = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-date", "mode"]
        unique_together = ("date", "mode")
        indexes = [
            models.Index(fields=["date", "mode"]),
            models.Index(fields=["mode", "date"]),
            models.Index(fields=["tone", "date"]),
        ]

    def __str__(self) -> str:
        return f"{self.date} {self.mode} {self.summary_title}"