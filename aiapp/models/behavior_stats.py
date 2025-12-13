# aiapp/models/behavior_stats.py
# -*- coding: utf-8 -*-
from __future__ import annotations

from django.db import models
from django.utils import timezone


class BehaviorStats(models.Model):
    """
    BehaviorStats（本番用⭐️信頼度の根拠DB）

    目的:
      - code × mode_period × mode_aggr ごとに、
        「直近N日」の紙シミュ実績サマリを保持し、
        picks_build/confidence_service が参照して⭐️を決める。

    重要:
      - stars だけでなく、n(試行数) や win_rate 等を正式に保持することで、
        データが少ない銘柄の過信を防ぎ、育つほど重みが上がる。
    """

    MODE_PERIOD_CHOICES = [
        ("short", "short"),
        ("mid", "mid"),
        ("long", "long"),
        ("all", "all"),
    ]
    MODE_AGGR_CHOICES = [
        ("aggr", "aggr"),
        ("norm", "norm"),
        ("def", "def"),
        ("all", "all"),
    ]

    # --- key ---
    code = models.CharField(max_length=10, db_index=True)
    mode_period = models.CharField(max_length=10, choices=MODE_PERIOD_CHOICES, db_index=True)
    mode_aggr = models.CharField(max_length=10, choices=MODE_AGGR_CHOICES, db_index=True)

    # --- headline ---
    stars = models.PositiveSmallIntegerField(default=1)

    # --- learning summary (new) ---
    n = models.PositiveIntegerField(default=0)          # 試行数（ラベルが win/lose/flat のもの）
    win = models.PositiveIntegerField(default=0)
    lose = models.PositiveIntegerField(default=0)
    flat = models.PositiveIntegerField(default=0)
    win_rate = models.FloatField(default=0.0)          # 0..100（%）

    avg_pl = models.FloatField(null=True, blank=True)  # 直近N日平均損益（円）
    std_pl = models.FloatField(null=True, blank=True)  # 損益の標準偏差（円）

    # 評価ウィンドウ（再現性/監査用）
    window_days = models.PositiveIntegerField(default=90)

    updated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        unique_together = (("code", "mode_period", "mode_aggr"),)
        indexes = [
            models.Index(fields=["mode_period", "mode_aggr", "stars"]),
            models.Index(fields=["mode_period", "mode_aggr", "n"]),
        ]

    def __str__(self) -> str:
        return f"{self.code} {self.mode_period}/{self.mode_aggr} stars={self.stars} n={self.n}"