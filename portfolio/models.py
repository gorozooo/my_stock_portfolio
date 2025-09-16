from django.conf import settings
from django.db import models

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