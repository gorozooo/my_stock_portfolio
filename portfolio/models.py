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

# 例）RealizedTrade モデル
class RealizedTrade(models.Model):
    user      = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    trade_at  = models.DateField()
    side      = models.CharField(max_length=4, choices=(("SELL","SELL"),("BUY","BUY")))
    ticker    = models.CharField(max_length=20)
    qty       = models.IntegerField()
    price     = models.DecimalField(max_digits=14, decimal_places=2)   # 売買単価
    basis     = models.DecimalField(max_digits=14, decimal_places=2,   # ★ 追加：原価(平均取得単価/1株)
                                    null=True, blank=True)
    fee       = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    tax       = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    memo      = models.TextField(blank=True, default="")
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
