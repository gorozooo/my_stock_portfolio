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
        DEPOSIT = "DEPOSIT", "入金"
        WITHDRAW = "WITHDRAW", "出金"
        FEE = "FEE", "手数料"
        TAX = "TAX", "税金"
        INTEREST = "INTEREST", "金利"
        DIVIDEND_NET = "DIVIDEND_NET", "配当(税引後)"
        XFER_IN = "XFER_IN", "振替(入)"
        XFER_OUT = "XFER_OUT", "振替(出)"
        TRADE_BUY = "TRADE_BUY", "現物買付(受渡)"
        TRADE_SELL = "TRADE_SELL", "現物売却(受渡)"
        REALIZED_PL = "REALIZED_PL", "実現損益調整"
        ADJUST = "ADJUST", "調整"

    account = models.ForeignKey(BrokerAccount, on_delete=models.CASCADE)
    at = models.DateTimeField(default=timezone.now)
    amount = models.IntegerField(help_text="入金は+、出金は-")
    kind = models.CharField(max_length=20, choices=Kind.choices)
    memo = models.CharField(max_length=200, blank=True, default="")
    link_model = models.CharField(max_length=50, blank=True, default="")
    link_id = models.IntegerField(null=True, blank=True)

    class Meta:
        ordering = ["-at", "-id"]

class MarginState(models.Model):
    """信用余力スナップショット（まずは手入力でOK）"""
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