# portfolio/models_cash.py
from __future__ import annotations
from django.db import models
from django.utils import timezone

class BrokerAccount(models.Model):
    """証券会社 × 口座区分 × 通貨 の“財布”"""
    BROKER_CHOICES = [("SBI","SBI"),("楽天","楽天"),("松井","松井"),("moomoo","moomoo")]
    ACCOUNT_CHOICES = [("現物","現物"),("NISA","NISA"),("信用","信用")]

    broker = models.CharField(max_length=20, choices=BROKER_CHOICES)
    account_type = models.CharField(max_length=10, choices=ACCOUNT_CHOICES)
    currency = models.CharField(max_length=3, default="JPY")
    opening_balance = models.IntegerField(default=0)
    name = models.CharField(max_length=50, blank=True, default="")

    class Meta:
        unique_together = ("broker", "account_type", "currency")

    def __str__(self) -> str:
        label = f"{self.broker}/{self.account_type}"
        if self.name:
            label += f" - {self.name}"
        return f"{label} ({self.currency})"


class CashLedger(models.Model):
    class Kind(models.TextChoices):
        DEPOSIT  = "DEPOSIT",  "入金"
        WITHDRAW = "WITHDRAW", "出金"
        XFER_IN  = "XFER_IN",  "振替入金"
        XFER_OUT = "XFER_OUT", "振替出金"

    class SourceType(models.TextChoices):
        DIVIDEND = "DIV",   "Dividend"
        REALIZED = "REAL",  "RealizedTrade"
        HOLDING  = "HOLD",  "Holding 初回買付"   # ★ 追加

    account = models.ForeignKey(
        BrokerAccount, on_delete=models.CASCADE, related_name="ledgers"
    )
    amount  = models.BigIntegerField(help_text="現金増減。入金は＋、出金は−")
    kind    = models.CharField(max_length=16, choices=Kind.choices)
    memo    = models.CharField(max_length=255, blank=True, default="")

    # ★ 発生日をそのまま保存（登録日固定にしない）
    at      = models.DateField(default=timezone.localdate)

    # 任意: どの保有に紐づくか（配当/実損は None 可）
    holding = models.ForeignKey(
        "portfolio.Holding", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="cash_ledgers"
    )

    # ソース一意キー
    source_type = models.CharField(
        max_length=8, choices=SourceType.choices, null=True, blank=True, db_index=True
    )
    source_id = models.BigIntegerField(null=True, blank=True, db_index=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["account", "source_type", "source_id"],
                condition=models.Q(source_type__isnull=False, source_id__isnull=False),
                name="uniq_cash_source_per_account",
            ),
        ]
        indexes = [
            models.Index(fields=["at"]),
            models.Index(fields=["source_type", "source_id"]),
        ]

    def __str__(self):
        src = f"{self.source_type}:{self.source_id}" if self.source_type and self.source_id else "-"
        return f"[{self.at}] {self.account} {self.amount} {self.kind} ({src})"


class MarginState(models.Model):
    """信用余力スナップショット（管理画面用）"""
    account = models.ForeignKey(BrokerAccount, on_delete=models.CASCADE)
    as_of = models.DateField()
    cash_free = models.IntegerField(default=0)
    stock_collateral_value = models.IntegerField(default=0)
    haircut_pct = models.FloatField(default=0.3)
    required_margin = models.IntegerField(default=0)
    restricted_amount = models.IntegerField(default=0)

    class Meta:
        unique_together = ("account", "as_of")

    @property
    def collateral_usable(self) -> int:
        return int(self.stock_collateral_value * (1.0 - self.haircut_pct))

    @property
    def available_funds(self) -> int:
        return int(self.cash_free + self.collateral_usable - self.required_margin - self.restricted_amount)