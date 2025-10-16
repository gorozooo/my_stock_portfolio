# portfolio/models_advisor.py
# -*- coding: utf-8 -*-
from __future__ import annotations
from django.db import models
from django.utils import timezone


class AdviceSession(models.Model):
    """1回のAIアドバイザー分析セッション（KPI/セクターのスナップショット単位）"""
    created_at = models.DateTimeField(default=timezone.now)
    context_json = models.JSONField(default=dict)  # KPIやセクターなどのスナップショット
    note = models.CharField(max_length=200, blank=True, default="")
    # A/B実験バリアント（'A' or 'B'）
    variant = models.CharField(max_length=1, default="A", db_index=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self) -> str:
        return f"Session {self.id} ({self.created_at:%Y-%m-%d})"


class AdviceItem(models.Model):
    """個別アドバイス（セッション内の1行）"""
    class Kind(models.TextChoices):
        REDUCE_MARGIN = "REDUCE_MARGIN", "信用圧縮"
        TRIM_WINNERS  = "TRIM_WINNERS",  "含み益上位の部分利確"
        ADD_CASH      = "ADD_CASH",      "現金比率引上げ"
        REBALANCE     = "REBALANCE",     "リバランス"
        CUT_LOSERS    = "CUT_LOSERS",    "含み損下位の整理"
        GENERAL       = "GENERAL",       "一般助言"

    session = models.ForeignKey(AdviceSession, on_delete=models.CASCADE, related_name="items")
    kind = models.CharField(max_length=32, choices=Kind.choices, default=Kind.REBALANCE)
    message = models.CharField(max_length=500)
    score = models.FloatField(default=0.0)
    reasons = models.JSONField(default=list)
    taken = models.BooleanField(default=False)  # UIで✅
    outcome = models.JSONField(null=True, blank=True)  # 後日結果（学習スクリプトが埋める）
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["kind"]),
            models.Index(fields=["taken"]),
        ]

    def __str__(self) -> str:
        return f"[{self.kind}] {self.message[:40]}"


# ========= 追加：学習用の素データ（特徴量＋ラベル） =========
class AdvisorProposal(models.Model):
    """
    学習データ1件＝「この助言項目をこの時点の特徴量で提示し、採用されたか？」
    - features: その時のKPI/セクターなどの特徴量（辞書）
    - label_taken: その助言が採用（True）/未採用（False）
    """
    item = models.ForeignKey(
        AdviceItem,
        on_delete=models.CASCADE,
        related_name="proposals",
        help_text="元となった助言アイテム"
    )
    features = models.JSONField(default=dict, blank=True)
    label_taken = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["label_taken"]),
        ]

    def __str__(self) -> str:
        lbl = "TAKEN" if self.label_taken else "SKIPPED"
        return f"Proposal#{self.id} {lbl} item={self.item_id}"


class AdvicePolicy(models.Model):
    """
    推論用ポリシー（学習の結果）
    - params … 係数や閾値、正規化パラメータなど（JSON）
    - model_blob … 学習済みモデル（pickle/joblib）をバイナリで保持したいとき用（任意）
    """
    class Kind(models.TextChoices):
        LINEAR = "LINEAR", "Linear"
        LOGREG = "LOGREG", "Logistic Regression"
        SKLEARN = "SKLEARN", "sklearn Model"

    kind = models.CharField(max_length=16, choices=Kind.choices, default=Kind.LOGREG)
    params = models.JSONField(default=dict, blank=True)
    model_blob = models.BinaryField(null=True, blank=True)
    enabled = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at", "-id"]

    def __str__(self) -> str:
        flag = "ON" if self.enabled else "OFF"
        return f"AdvicePolicy#{self.id} {self.kind} ({flag})"


class AdvisorMetrics(models.Model):
    """
    学習精度のモニタリングログ（学習エンジン別）
    - advisor_train などの学習コマンドが1回走るごとに1行追加
    """
    ENGINE_CHOICES = (
        ("logreg", "LogisticRegression"),
        ("gbdt", "GradientBoosting"),
        ("lgbm", "LightGBM"),
        ("rule", "RuleOnly"),
        ("mix", "Rule+Model"),
    )

    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    engine = models.CharField(max_length=20, choices=ENGINE_CHOICES, default="logreg")
    policy = models.ForeignKey(
        AdvicePolicy, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="metrics"
    )
    train_acc = models.FloatField(help_text="学習時の推定精度（0..1）")
    n = models.IntegerField(help_text="学習に使ったサンプル件数")
    notes = models.JSONField(default=dict, blank=True)  # {"horizon":7, "features":[...]} 等

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self) -> str:
        return f"[{self.created_at:%Y-%m-%d %H:%M}] {self.engine} acc={self.train_acc:.3f} n={self.n}"