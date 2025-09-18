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
    BROKER_CHOICES = (
        ("RAKUTEN", "楽天証券"),
        ("SBI",     "SBI証券"),
        ("MATSUI",  "松井証券"),
        ("OTHER",   "その他"),
    )

    user      = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    trade_at  = models.DateField()
    side      = models.CharField(max_length=4, choices=(("SELL","SELL"),("BUY","BUY")))
    ticker    = models.CharField(max_length=20)
    name      = models.CharField(max_length=120, blank=True, default="")   # 既存にある想定。無ければ追加してOK
    qty       = models.IntegerField()
    price     = models.DecimalField(max_digits=14, decimal_places=2)       # 約定単価（1株あたり）
    basis     = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    fee       = models.DecimalField(max_digits=14, decimal_places=2, default=0)  # 手数料（税含め一本化運用でもOK）
    tax       = models.DecimalField(max_digits=14, decimal_places=2, default=0)  # 使わないなら将来削除可

    # ★ 追加: 証券会社（任意）/ 受渡金額（現金フロー、手入力可）
    broker    = models.CharField(max_length=16, choices=BROKER_CHOICES, default="OTHER")
    cashflow  = models.DecimalField(
        max_digits=16, decimal_places=2, null=True, blank=True,
        help_text="受渡金額（現金フロー）。SELL=受取は＋、BUY=支払は−。未入力なら自動計算。"
    )

    memo      = models.TextField(blank=True, default="")
    created_at= models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-trade_at","-id"]

    @property
    def amount(self):
        """約定金額（絶対値ではなく ‘価格×数量’。符号は side で扱う）"""
        return float(self.qty) * float(self.price)

    @property
    def pnl(self):
        """
        投資家の実現損益（PnL）。basis を使った純粋損益。
        SELL: (price - basis) * qty - fee - tax
        BUY : 0（建玉オープンとして損益は発生させない）
        basis 未設定時は price を代用（=損益0）で安全に倒す。
        """
        if self.side == "BUY":
            gross = 0.0
        else:
            b = float(self.basis) if self.basis is not None else float(self.price)
            gross = (float(self.price) - b) * float(self.qty)

        return gross - float(self.fee) - float(self.tax)

    @property
    def cashflow_effective(self):
        """
        表示・集計で使う“現金の動き”。
        - 入力がある: そのまま返す（SELL=＋, BUY=− が望ましい）
        - 入力なし  : 受渡の慣習に沿って自動推定
            SELL → + (qty*price - fee - tax)
            BUY  → - (qty*price + fee + tax)
        """
        if self.cashflow is not None:
            return float(self.cashflow)

        signed = self.amount if self.side == "SELL" else -self.amount
        return signed - float(self.fee) - float(self.tax)
        