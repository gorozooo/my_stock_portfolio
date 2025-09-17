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

class RealizedTrade(models.Model):
    user      = models.ForeignKey(User, on_delete=models.CASCADE, related_name="realized_trades")
    trade_at  = models.DateField(db_index=True)
    ticker    = models.CharField(max_length=24, db_index=True)
    side      = models.CharField(max_length=4, choices=[("BUY","BUY"),("SELL","SELL")], default="SELL")
    qty       = models.IntegerField()
    price     = models.DecimalField(max_digits=14, decimal_places=4)
    fee       = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    tax       = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    memo      = models.CharField(max_length=200, blank=True)
    created_at= models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-trade_at","-id"]

    @property
    def amount(self):  # 約定金額
        return float(self.qty) * float(self.price)

    @property
    def pnl(self):     # SELL を正とする簡易実現損益（手数料・税引き後）
        gross = (1 if self.side=="SELL" else -1) * self.amount
        return gross - float(self.fee) - float(self.tax)
