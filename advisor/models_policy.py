# advisor/models_policy.py
from __future__ import annotations
from django.db import models
from django.contrib.auth import get_user_model

User = get_user_model()


class AdvisorPolicy(models.Model):
    """
    取引ルールの”元データ”。
    - rule_json にエントリー/イグジット/サイズ/リスク/時間軸などの数字を保存。
    - is_active=True のものが当日のスクリーニングに使われる想定。
    """
    name = models.CharField(max_length=120, unique=True)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    priority = models.IntegerField(default=50)  # 高いほど先に評価
    rule_json = models.JSONField(default=dict)  # ルール本体（JSON/YAML由来）

    # 任意：戦略の系統ラベル（例: "NISA", "Trend", "MeanReversion" など）
    family = models.CharField(max_length=40, blank=True, default="")

    # 任意：想定の時間軸表示（UI向け）
    timeframe_label = models.CharField(max_length=60, blank=True, default="中期（20〜45日）")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-is_active", "-priority", "name")

    def __str__(self):
        return f"{self.name} (prio={self.priority}, active={self.is_active})"


class PolicySnapshot(models.Model):
    """
    実行時点の”固定コピー”。後から完全再現するための証拠。
    - file_path は media/advisor/policies/yyyymmdd/<slug>.json を想定。
    """
    policy = models.ForeignKey(AdvisorPolicy, on_delete=models.CASCADE, related_name="snapshots")
    as_of = models.DateField(db_index=True)  # スナップショットの日付（JST）
    version_tag = models.CharField(max_length=40, blank=True, default="")  # 例: '2025-10-30-am'
    payload = models.JSONField(default=dict)  # スナップショット実体
    file_path = models.CharField(max_length=255, blank=True, default="")  # 書き出し先（相対）

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = (("policy", "as_of", "version_tag"),)
        ordering = ("-as_of", "-created_at")

    def __str__(self):
        return f"{self.as_of} {self.policy.name} {self.version_tag or ''}".strip()


class DeviationLog(models.Model):
    """
    ルール逸脱の記録（裁量でもOK、理由が残れば良い）
    """
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    policy = models.ForeignKey(AdvisorPolicy, on_delete=models.SET_NULL, null=True, blank=True)
    ticker = models.CharField(max_length=20, blank=True, default="")
    action = models.CharField(max_length=60, blank=True, default="")  # 例: 'manual_entry', 'manual_exit'
    reason = models.TextField(blank=True, default="")  # なぜルールから外れたか
    evidence_url = models.URLField(blank=True, default="")  # スクショ等のURL（任意）
    extra = models.JSONField(default=dict)  # その時の指標などを添付可

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["created_at"]),
            models.Index(fields=["ticker"]),
        ]
        ordering = ("-created_at",)

    def __str__(self):
        return f"[{self.created_at:%Y-%m-%d %H:%M}] {self.ticker} {self.action}"