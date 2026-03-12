"""
[FILE] models.py
[PATH] <project_root>/shihyo/models.py

このファイルは何？
- 指標専用アプリ shihyo のDBモデルです。
- 既存の
  1) MarketIndicatorSnapshot
  2) ShihyoDailyPrice
  3) ShihyoIntradayPrice
  4) ShihyoMarketBiasSnapshot
  5) ShihyoPreopenFeatureSnapshot
  6) ShihyoPreopenPredictionSnapshot
  7) ShihyoPreopenWeeklyReview
  を定義します。

今回の修正ポイント：
- ShihyoPreopenPredictionSnapshot の slot に open_1000 を追加
- これにより、朝7:00予想(preopen_0700) と 10:00再予想(open_1000) を
  同じ予測保存テーブルで管理します
- ShihyoPreopenWeeklyReview に、人の承認状態
  （未確認 / 承認 / 保留 / 却下）を追加します
"""

from django.conf import settings
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

    # --- nikkei spot previous close (正式保存) ---
    nikkei_spot_previous_close = models.FloatField(null=True, blank=True)

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


class ShihyoPreopenFeatureSnapshot(models.Model):
    """
    朝7:00モデルの学習用特徴量 1行保存。
    1行 = 1営業日の朝7:00時点で見えていた情報だけ。
    後から引け値が確定したら target_* を埋める。
    """
    SLOT_PREOPEN_0700 = "preopen_0700"

    SLOT_CHOICES = [
        (SLOT_PREOPEN_0700, "朝7:00"),
    ]

    TARGET_UP = "UP"
    TARGET_FLAT = "FLAT"
    TARGET_DOWN = "DOWN"

    TARGET_DIRECTION_CHOICES = [
        (TARGET_UP, "上昇"),
        (TARGET_FLAT, "様子見"),
        (TARGET_DOWN, "下落"),
    ]

    trade_date = models.DateField(db_index=True)
    slot = models.CharField(max_length=32, choices=SLOT_CHOICES, default=SLOT_PREOPEN_0700, db_index=True)
    asof_at = models.DateTimeField(db_index=True)
    market = models.CharField(max_length=16, default="JP", db_index=True)

    # --- 基準価格 ---
    prev_close_n225 = models.FloatField(null=True, blank=True)
    prev_open_n225 = models.FloatField(null=True, blank=True)
    prev_high_n225 = models.FloatField(null=True, blank=True)
    prev_low_n225 = models.FloatField(null=True, blank=True)

    # --- 外部環境 MVP ---
    nikkei_futures_last = models.FloatField(null=True, blank=True)
    nikkei_futures_pct_vs_prev_close_n225 = models.FloatField(null=True, blank=True)
    nikkei_futures_gap_pts = models.FloatField(null=True, blank=True)

    usdjpy_last = models.FloatField(null=True, blank=True)
    usdjpy_pct_1d = models.FloatField(null=True, blank=True)

    vix_last = models.FloatField(null=True, blank=True)
    vix_pct_1d = models.FloatField(null=True, blank=True)

    sp500_pct_1d = models.FloatField(null=True, blank=True)
    nasdaq100_pct_1d = models.FloatField(null=True, blank=True)
    sox_pct_1d = models.FloatField(null=True, blank=True)

    # --- 前営業日の日本株内部状態 MVP ---
    prev_market_bias_tone = models.CharField(max_length=32, default="", blank=True)
    prev_strong_sector_1 = models.CharField(max_length=255, default="", blank=True)
    prev_strong_sector_2 = models.CharField(max_length=255, default="", blank=True)
    prev_strong_sector_3 = models.CharField(max_length=255, default="", blank=True)
    prev_weak_sector_1 = models.CharField(max_length=255, default="", blank=True)
    prev_weak_sector_2 = models.CharField(max_length=255, default="", blank=True)
    prev_weak_sector_3 = models.CharField(max_length=255, default="", blank=True)

    # --- カレンダー特徴 MVP ---
    weekday = models.PositiveSmallIntegerField(default=0)
    month = models.PositiveSmallIntegerField(default=0)
    is_sq_week = models.BooleanField(default=False, db_index=True)
    is_major_holiday_adjacent = models.BooleanField(default=False)

    # --- 旧ルールベース判定 ---
    risk_score_legacy = models.FloatField(null=True, blank=True)
    risk_title_legacy = models.CharField(max_length=64, default="", blank=True)
    nikkei_label_legacy = models.CharField(max_length=32, default="", blank=True)
    fx_label_legacy = models.CharField(max_length=32, default="", blank=True)
    vix_label_legacy = models.CharField(max_length=32, default="", blank=True)

    # --- 正解データ（引け後に確定） ---
    target_close_value = models.FloatField(null=True, blank=True)
    target_close_pct = models.FloatField(null=True, blank=True)
    target_direction_3 = models.CharField(
        max_length=8,
        choices=TARGET_DIRECTION_CHOICES,
        default="",
        blank=True,
        db_index=True,
    )
    is_target_fixed = models.BooleanField(default=False, db_index=True)

    # --- バージョン / 再現性 ---
    feature_version = models.CharField(max_length=64, default="preopen_feature_v1", db_index=True)
    source_payload = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-trade_date", "slot"]
        unique_together = ("trade_date", "slot")
        indexes = [
            models.Index(fields=["trade_date", "slot"]),
            models.Index(fields=["slot", "is_target_fixed", "trade_date"]),
            models.Index(fields=["feature_version", "trade_date"]),
        ]

    def __str__(self) -> str:
        return f"{self.trade_date} {self.slot} feature"


class ShihyoPreopenPredictionSnapshot(models.Model):
    """
    予測結果保存。
    - preopen_0700 : 朝7:00予想
    - open_1000    : 10:00再予想
    画面表示・LINE通知・精度検証に使う。
    """
    SLOT_PREOPEN_0700 = "preopen_0700"
    SLOT_OPEN_1000 = "open_1000"

    SLOT_CHOICES = [
        (SLOT_PREOPEN_0700, "朝7:00"),
        (SLOT_OPEN_1000, "10:00再予想"),
    ]

    PRED_UP = "UP"
    PRED_FLAT = "FLAT"
    PRED_DOWN = "DOWN"

    PRED_DIRECTION_CHOICES = [
        (PRED_UP, "上昇"),
        (PRED_FLAT, "様子見"),
        (PRED_DOWN, "下落"),
    ]

    trade_date = models.DateField(db_index=True)
    slot = models.CharField(max_length=32, choices=SLOT_CHOICES, default=SLOT_PREOPEN_0700, db_index=True)
    predicted_at = models.DateTimeField(db_index=True)

    model_version = models.CharField(max_length=64, default="rule_v1", db_index=True)
    feature_version = models.CharField(max_length=64, default="preopen_feature_v1", db_index=True)

    reference_close_n225 = models.FloatField(null=True, blank=True)

    pred_direction = models.CharField(
        max_length=8,
        choices=PRED_DIRECTION_CHOICES,
        default="",
        blank=True,
        db_index=True,
    )
    pred_up_prob = models.FloatField(null=True, blank=True)
    pred_down_prob = models.FloatField(null=True, blank=True)
    pred_flat_prob = models.FloatField(null=True, blank=True)

    pred_close_pct = models.FloatField(null=True, blank=True)
    pred_close_value = models.FloatField(null=True, blank=True)
    pred_confidence = models.FloatField(null=True, blank=True)

    display_label = models.CharField(max_length=64, default="", blank=True)
    display_reason_1 = models.CharField(max_length=255, default="", blank=True)
    display_reason_2 = models.CharField(max_length=255, default="", blank=True)
    display_reason_3 = models.CharField(max_length=255, default="", blank=True)

    # --- 実績 / 評価 ---
    actual_close_value = models.FloatField(null=True, blank=True)
    actual_close_pct = models.FloatField(null=True, blank=True)
    actual_direction_3 = models.CharField(max_length=8, default="", blank=True)
    hit_direction = models.BooleanField(null=True, blank=True)
    abs_error_pct = models.FloatField(null=True, blank=True)
    evaluated_at = models.DateTimeField(null=True, blank=True)

    feature_snapshot = models.ForeignKey(
        ShihyoPreopenFeatureSnapshot,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="predictions",
    )
    raw_prediction_payload = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-trade_date", "slot", "-predicted_at"]
        unique_together = ("trade_date", "slot", "model_version")
        indexes = [
            models.Index(fields=["trade_date", "slot", "model_version"]),
            models.Index(fields=["model_version", "trade_date"]),
            models.Index(fields=["feature_version", "trade_date"]),
        ]

    def __str__(self) -> str:
        return f"{self.trade_date} {self.slot} {self.model_version}"


class ShihyoPreopenWeeklyReview(models.Model):
    """
    朝7:00モデルの週次レビュー保存。
    1行 = 1週間分の精度、最大弱点、次の追加候補1件。
    """
    SLOT_PREOPEN_0700 = "preopen_0700"

    SLOT_CHOICES = [
        (SLOT_PREOPEN_0700, "朝7:00"),
    ]

    STATUS_STABLE = "stable"
    STATUS_PROPOSAL_READY = "proposal_ready"
    STATUS_COOLDOWN = "cooldown"
    STATUS_INSUFFICIENT_DATA = "insufficient_data"
    STATUS_PROPOSAL_APPLIED = "proposal_applied"
    STATUS_PROPOSAL_ADOPTED = "proposal_adopted"
    STATUS_PROPOSAL_REJECTED = "proposal_rejected"

    STATUS_CHOICES = [
        (STATUS_STABLE, "安定"),
        (STATUS_PROPOSAL_READY, "候補あり"),
        (STATUS_COOLDOWN, "クールダウン"),
        (STATUS_INSUFFICIENT_DATA, "データ不足"),
        (STATUS_PROPOSAL_APPLIED, "比較中"),
        (STATUS_PROPOSAL_ADOPTED, "採用"),
        (STATUS_PROPOSAL_REJECTED, "不採用"),
    ]

    COMPARISON_PENDING = "pending"
    COMPARISON_ADOPT = "adopt"
    COMPARISON_REJECT = "reject"

    COMPARISON_RESULT_CHOICES = [
        (COMPARISON_PENDING, "比較待ち"),
        (COMPARISON_ADOPT, "採用"),
        (COMPARISON_REJECT, "不採用"),
    ]

    APPROVAL_PENDING = "pending"
    APPROVAL_APPROVED = "approved"
    APPROVAL_HOLD = "hold"
    APPROVAL_REJECTED = "rejected"

    APPROVAL_CHOICES = [
        (APPROVAL_PENDING, "未確認"),
        (APPROVAL_APPROVED, "承認"),
        (APPROVAL_HOLD, "保留"),
        (APPROVAL_REJECTED, "却下"),
    ]

    week_start_date = models.DateField(db_index=True)
    week_end_date = models.DateField(db_index=True)

    slot = models.CharField(max_length=32, choices=SLOT_CHOICES, default=SLOT_PREOPEN_0700, db_index=True)
    model_version = models.CharField(max_length=64, default="rule_v1", db_index=True)
    feature_version = models.CharField(max_length=64, default="preopen_feature_v1", db_index=True)

    # --- 全体成績 ---
    sample_count = models.PositiveIntegerField(default=0)
    direction_accuracy = models.FloatField(null=True, blank=True)

    high_conf_sample_count = models.PositiveIntegerField(default=0)
    high_conf_direction_accuracy = models.FloatField(null=True, blank=True)

    mean_abs_error_pct = models.FloatField(null=True, blank=True)
    median_abs_error_pct = models.FloatField(null=True, blank=True)
    big_miss_count = models.PositiveIntegerField(default=0)
    big_miss_ratio = models.FloatField(null=True, blank=True)

    # --- 条件別弱点集計（MVPで主要だけ） ---
    sq_week_count = models.PositiveIntegerField(default=0)
    sq_week_accuracy = models.FloatField(null=True, blank=True)

    vix_spike_count = models.PositiveIntegerField(default=0)
    vix_spike_accuracy = models.FloatField(null=True, blank=True)

    futures_gap_count = models.PositiveIntegerField(default=0)
    futures_gap_accuracy = models.FloatField(null=True, blank=True)

    fx_shock_count = models.PositiveIntegerField(default=0)
    fx_shock_accuracy = models.FloatField(null=True, blank=True)

    # --- 最大弱点 ---
    weakness_code = models.CharField(max_length=64, default="", blank=True)
    weakness_label = models.CharField(max_length=255, default="", blank=True)
    weakness_sample_count = models.PositiveIntegerField(default=0)
    weakness_accuracy = models.FloatField(null=True, blank=True)
    weakness_gap_vs_overall = models.FloatField(null=True, blank=True)
    weakness_high_conf_gap_vs_overall = models.FloatField(null=True, blank=True)

    # --- 次の追加候補 ---
    proposal_code = models.CharField(max_length=64, default="", blank=True)
    proposal_label = models.CharField(max_length=255, default="", blank=True)
    proposal_feature_group = models.CharField(max_length=64, default="", blank=True)
    proposal_reason = models.TextField(default="", blank=True)
    proposal_priority = models.PositiveSmallIntegerField(default=0)
    proposal_cost_level = models.CharField(max_length=16, default="", blank=True)

    status = models.CharField(
        max_length=32,
        choices=STATUS_CHOICES,
        default=STATUS_INSUFFICIENT_DATA,
        db_index=True,
    )

    approval_status = models.CharField(
        max_length=16,
        choices=APPROVAL_CHOICES,
        default=APPROVAL_PENDING,
        db_index=True,
    )
    approval_comment = models.TextField(default="", blank=True)
    approval_updated_at = models.DateTimeField(null=True, blank=True)
    approval_updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="shihyo_weekly_review_approvals",
    )

    # --- 比較結果 ---
    comparison_target_model_version = models.CharField(max_length=64, default="", blank=True)
    comparison_target_feature_version = models.CharField(max_length=64, default="", blank=True)
    accuracy_diff = models.FloatField(null=True, blank=True)
    high_conf_accuracy_diff = models.FloatField(null=True, blank=True)
    mae_diff = models.FloatField(null=True, blank=True)
    comparison_result = models.CharField(
        max_length=16,
        choices=COMPARISON_RESULT_CHOICES,
        default=COMPARISON_PENDING,
        db_index=True,
    )

    review_payload = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-week_start_date", "-created_at"]
        unique_together = ("week_start_date", "week_end_date", "slot", "model_version", "feature_version")
        indexes = [
            models.Index(fields=["week_start_date", "week_end_date"]),
            models.Index(fields=["status", "week_start_date"]),
            models.Index(fields=["approval_status", "week_start_date"]),
            models.Index(fields=["model_version", "week_start_date"]),
            models.Index(fields=["feature_version", "week_start_date"]),
        ]

    def __str__(self) -> str:
        return f"{self.week_start_date} - {self.week_end_date} {self.model_version}"