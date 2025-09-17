from django.conf import settings
from django.db import models
from django.contrib.auth import get_user_model

User = get_user_model()

class UserSetting(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    account_equity = models.BigIntegerField("口座残高(円)", default=1_000_000)  # デフォルト100万
    risk_pct = models.FloatField("1トレードのリスク％", default=1.0)

    def __str__(self):
        return f"{self.user.username} 設定"
        

class Holding(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    ticker = models.CharField(max_length=16)
    name = models.CharField(max_length=128, blank=True)
    quantity = models.IntegerField(default=0)
    avg_cost = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    market = models.CharField(max_length=16, blank=True)  # JP/US など
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self):
        return f"{self.ticker} x{self.quantity}"

# portfolio/models.py

from decimal import Decimal, ROUND_HALF_UP
from django.conf import settings
from django.db import models


class RealizedTrade(models.Model):
    user      = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    trade_at  = models.DateField()

    SIDE_CHOICES = (("SELL", "SELL"), ("BUY", "BUY"))
    side      = models.CharField(max_length=4, choices=SIDE_CHOICES)

    ticker    = models.CharField(max_length=20)       # 証券コード
    name      = models.CharField(max_length=100, null=True, blank=True)  # ★ 変更
    qty       = models.IntegerField()

    price     = models.DecimalField(max_digits=14, decimal_places=2)  # 売買単価
    basis     = models.DecimalField(                              # 原価（1株あたり平均取得単価）
        max_digits=14, decimal_places=2, null=True, blank=True
    )

    fee       = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    tax       = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    memo      = models.TextField(blank=True, default="")
    created_at= models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-trade_at", "-id"]

    # ------------------------------------------------------------------
    @staticmethod
    def _D(x) -> Decimal:
        """Decimal 変換（None → 0）"""
        if x is None:
            return Decimal("0")
        if isinstance(x, Decimal):
            return x
        return Decimal(str(x))

    @property
    def amount(self) -> Decimal:
        """約定金額 = 単価 × 数量"""
        return self._D(self.price) * Decimal(self.qty)

    @property
    def pnl(self) -> Decimal:
        """
        実現損益（SELLを正）
        SELL: (price - basis) * qty - fee - tax
        BUY : -((price - basis) * qty) - fee - tax
        """
        price = self._D(self.price)
        basis = self._D(self.basis)  # None → 0
        fee   = self._D(self.fee)
        tax   = self._D(self.tax)
        qty   = Decimal(self.qty)

        core = (price - basis) * qty
        if self.side == "SELL":
            pnl = core - fee - tax
        else:
            pnl = -core - fee - tax

        return pnl.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    def __str__(self) -> str:
        return f"{self.trade_at} {self.ticker} {self.name} {self.side} x{self.qty} @ {self.price}"