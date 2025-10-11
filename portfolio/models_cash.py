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

    # ★ auto_now_add をやめて、“発生日” をそのまま入れられるように
    at      = models.DateField(default=timezone.localdate)

    # 任意：どの保有由来か辿れるように（配当/実損は None のままでOK）
    holding = models.ForeignKey(
        "portfolio.Holding", null=True, blank=True, on_delete=models.SET_NULL, related_name="cash_ledgers"
    )

    # ソース一意キー
    source_type = models.CharField(
        max_length=8, choices=SourceType.choices, null=True, blank=True, db_index=True
    )
    source_id = models.BigIntegerField(null=True, blank=True, db_index=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-at", "-id"]  # 発生日で並ぶほうが台帳に自然
        constraints = [
            # 同一口座・同一ソースは1行だけ（NULLは対象外）
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