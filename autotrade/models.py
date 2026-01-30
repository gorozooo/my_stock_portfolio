"""
[FILE] autotrade/models.py
[PATH] <project_root>/autotrade/models.py

このファイルは何？
- iPhone 1画面ダッシュボードに表示する「今日の状態」をDBに保存するモデルです。
- 1日1レコード（dateでユニーク）として、状態・バックテスト結果・銘柄・ルールをまとめて保持します。

初心者ポイント：
- 画面表示やログの中心になる“箱”です。
"""

from django.conf import settings
from django.db import models
from django.utils import timezone


class AutoTradeDailyState(models.Model):
    """
    iPhone 1画面に表示する「今日の状態」を1レコードに集約
    """
    date = models.DateField(unique=True)

    # 🟢 FULL / 🟡 LIGHT / 🔴 STOP
    gate_level = models.CharField(max_length=10, default="STOP")
    gate_reason = models.TextField(blank=True, default="")

    # 資産（将来：証券会社から取り込んで更新）
    equity_yen = models.BigIntegerField(default=getattr(settings, "AUTOTRADE_BASE_EQUITY_YEN", 1_000_000))
    pnl_day_yen = models.IntegerField(default=0)
    pnl_total_yen = models.IntegerField(default=0)

    # 今日の戦略：BREAKOUT / VWAP
    strategy = models.CharField(max_length=20, blank=True, default="")
    strategy_decided_at = models.DateTimeField(null=True, blank=True)

    # 今日の銘柄（5〜10）や理由（表示用）
    # 例: {"picks":[{"ticker":"7203.T","reason":"..."}]}
    universe = models.JSONField(default=dict, blank=True)

    # バックテスト結果（strategy別、window別）
    # 例: {"BREAKOUT":{"20":{...},"60":{...},"120":{...}}, "VWAP":{...}}
    backtest = models.JSONField(default=dict, blank=True)

    # 今日のルール要約（リスク、回数、同時ポジ、時間など）
    rules = models.JSONField(default=dict, blank=True)

    updated_at = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return f"[{self.date}] {self.gate_level} {self.strategy}".strip()