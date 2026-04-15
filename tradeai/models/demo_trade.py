# =========================================================
# [FILE] demo_trade.py
# [PATH] <project_root>/tradeai/models/demo_trade.py
#
# このファイルは何？
# - デモ売買（紙トレード）を保存するモデルです。
# - LONG / SHORT 両対応です。
# =========================================================

from django.conf import settings
from django.db import models
from django.utils import timezone


class DemoTrade(models.Model):
    class DirectionChoices(models.TextChoices):
        LONG = "LONG", "ロング"
        SHORT = "SHORT", "ショート"

    class SourceScopeChoices(models.TextChoices):
        HOLDING = "HOLDING", "保有銘柄"
        WATCHLIST = "WATCHLIST", "ウォッチリスト"
        UNIVERSE = "UNIVERSE", "母集団"

    class StatusChoices(models.TextChoices):
        OPEN = "OPEN", "保有中"
        CLOSED = "CLOSED", "終了"
        CANCELED = "CANCELED", "取消"

    class ResultLabelChoices(models.TextChoices):
        WIN = "WIN", "勝ち"
        LOSS = "LOSS", "負け"
        FLAT = "FLAT", "引き分け"
        UNKNOWN = "UNKNOWN", "未判定"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="tradeai_demo_trades",
    )

    signal_event = models.ForeignKey(
        "tradeai.SignalEvent",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="demo_trades",
    )

    ticker = models.CharField("証券コード", max_length=16, db_index=True)
    name = models.CharField("銘柄名", max_length=128, blank=True, default="")

    direction = models.CharField("方向", max_length=8, choices=DirectionChoices.choices)
    source_scope = models.CharField("発生元", max_length=16, choices=SourceScopeChoices.choices)

    status = models.CharField(
        "状態",
        max_length=16,
        choices=StatusChoices.choices,
        default=StatusChoices.OPEN,
    )
    result_label = models.CharField(
        "結果",
        max_length=16,
        choices=ResultLabelChoices.choices,
        default=ResultLabelChoices.UNKNOWN,
    )

    entry_at = models.DateTimeField("エントリー日時", default=timezone.now, db_index=True)
    entry_price = models.DecimalField("エントリー価格", max_digits=14, decimal_places=2)
    stop_price = models.DecimalField("ストップ価格", max_digits=14, decimal_places=2, null=True, blank=True)
    take_profit_price = models.DecimalField("利確価格", max_digits=14, decimal_places=2, null=True, blank=True)
    qty = models.PositiveIntegerField("数量", default=0)

    close_at = models.DateTimeField("クローズ日時", null=True, blank=True)
    close_price = models.DecimalField("クローズ価格", max_digits=14, decimal_places=2, null=True, blank=True)

    pnl_yen = models.DecimalField("損益額", max_digits=14, decimal_places=2, null=True, blank=True)
    pnl_pct = models.DecimalField("損益率", max_digits=8, decimal_places=2, null=True, blank=True)
    max_favorable_pct = models.DecimalField("最大有利率", max_digits=8, decimal_places=2, null=True, blank=True)
    max_adverse_pct = models.DecimalField("最大不利率", max_digits=8, decimal_places=2, null=True, blank=True)

    entry_reason_text = models.TextField("エントリー理由", blank=True, default="")
    entry_payload = models.JSONField("エントリー詳細", default=dict, blank=True)
    exit_reason = models.TextField("終了理由", blank=True, default="")

    created_at = models.DateTimeField("作成日時", auto_now_add=True)
    updated_at = models.DateTimeField("更新日時", auto_now=True)

    class Meta:
        ordering = ["-entry_at", "-id"]
        indexes = [
            models.Index(fields=["user", "status"]),
            models.Index(fields=["user", "ticker", "status"]),
            models.Index(fields=["direction", "status"]),
            models.Index(fields=["entry_at"]),
        ]

    def __str__(self):
        return f"{self.ticker} {self.direction} {self.status}"

    def save(self, *args, **kwargs):
        if self.ticker:
            self.ticker = self.ticker.upper().strip()
        super().save(*args, **kwargs)