from __future__ import annotations
from django.db import models
from django.utils import timezone
from django.contrib.auth import get_user_model

User = get_user_model()


class AdviceSession(models.Model):
    """1回のAIアドバイザー分析セッション（スナップショット単位）"""
    user = models.ForeignKey(User, on_delete=models.CASCADE, null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    context_json = models.JSONField(default=dict)  # KPIやセクターなどのスナップショット
    note = models.CharField(max_length=200, blank=True, default="")

    def __str__(self):
        return f"Session {self.id} ({self.created_at:%Y-%m-%d})"


class AdviceItem(models.Model):
    """個別アドバイス（画面に出す1行）"""
    class Kind(models.TextChoices):
        REDUCE_MARGIN = "REDUCE_MARGIN", "信用圧縮"
        TRIM_WINNERS  = "TRIM_WINNERS",  "含み益上位の部分利確"
        ADD_CASH      = "ADD_CASH",      "現金比率引上げ"
        REBALANCE     = "REBALANCE",     "リバランス"
        CUT_LOSERS    = "CUT_LOSERS",    "含み損下位の整理"
        GENERAL       = "GENERAL",       "その他"

    session = models.ForeignKey(AdviceSession, on_delete=models.CASCADE, related_name="items")
    kind = models.CharField(max_length=32, choices=Kind.choices, default=Kind.GENERAL)
    message = models.CharField(max_length=500)
    score = models.FloatField(default=0.0)
    reasons = models.JSONField(default=list)
    taken = models.BooleanField(default=False)  # ✅ 実行済み（UIトグル）
    outcome = models.JSONField(null=True, blank=True)  # 後日結果
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    def __str__(self):
        return f"[{self.kind}] {self.message[:40]}"


# ========= 学習用 追加モデル =========
class AdvisorProposal(models.Model):
    """
    学習ログ：提案（AdviceItem）に紐づく“特徴量と採否”
    - features: その時点の特徴量（KPIから派生）
    - label_taken: その提案をユーザが採用したか（✅）
    """
    item = models.OneToOneField(AdviceItem, on_delete=models.CASCADE, related_name="proposal")
    features = models.JSONField(default=dict)
    label_taken = models.BooleanField(default=False)  # この時点の採否ラベル
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    def __str__(self):
        return f"Proposal #{self.id} (item={self.item_id})"


class ProposalOutcome(models.Model):
    """
    学習ログ：採用後 n 日の成果（将来の教師データ）
    - horizon_days: 何日後の評価か
    - metrics: {"realized_delta":..., "total_assets_delta":..., ...}
    """
    proposal = models.ForeignKey(AdvisorProposal, on_delete=models.CASCADE, related_name="outcomes")
    horizon_days = models.PositiveSmallIntegerField(default=7)
    metrics = models.JSONField(default=dict)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        unique_together = ("proposal", "horizon_days")


class AdvicePolicy(models.Model):
    """
    推論ポリシー（係数 or モデル）
    - params: {"bias": float, "coef": {"feat1": w1, ...}, "norm": {"feat": {"mu":..,"sigma":..}} など}
    - model_blob: joblib/pickle で保存（scikit-learn等）。使わないなら空でOK。
    """
    class Kind(models.TextChoices):
        LINEAR = "LINEAR", "線形（係数のみ）"
        LOGREG = "LOGREG", "ロジスティック回帰"
        SKLEARN = "SKLEARN", "scikit-learn pickled"

    name = models.CharField(max_length=100, default="default")
    version = models.CharField(max_length=20, default="v1")
    enabled = models.BooleanField(default=False, db_index=True)

    kind = models.CharField(max_length=16, choices=Kind.choices, default=Kind.LINEAR)
    params = models.JSONField(default=dict)
    model_blob = models.BinaryField(null=True, blank=True)

    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("name", "version")

    def __str__(self):
        return f"Policy {self.name}/{self.version} ({'ON' if self.enabled else 'off'})"