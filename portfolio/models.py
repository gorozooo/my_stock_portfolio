from django.conf import settings
from django.db import models
from django.contrib.auth import get_user_model

User = get_user_model()


class UserSetting(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    account_equity = models.BigIntegerField("口座残高(円)", default=1_000_000)
    risk_pct = models.FloatField("1トレードのリスク％", default=1.0)

    def __str__(self):
        return f"{self.user.username} 設定"


# ==== Holding ============================================================
class Holding(models.Model):
    # user は残す（既存ビューの互換のため）
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)

    ticker = models.CharField(max_length=16)
    name   = models.CharField(max_length=128, blank=True)

    # 数量 / 平均取得単価
    quantity = models.IntegerField(default=0)
    avg_cost = models.DecimalField(max_digits=14, decimal_places=2, default=0)

    # ★ 追加: 証券会社 / 売買方向 / 口座区分
    BROKER_CHOICES = (
        ("RAKUTEN", "楽天証券"),
        ("SBI",     "SBI証券"),
        ("MATSUI",  "松井証券"),
        ("OTHER",   "その他"),
    )
    SIDE_CHOICES = (("BUY", "BUY"), ("SELL", "SELL"))
    ACCOUNT_CHOICES = (("SPEC", "特定"), ("MARGIN", "信用"), ("NISA", "NISA"))

    broker  = models.CharField(max_length=16, choices=BROKER_CHOICES, default="OTHER")
    side    = models.CharField(max_length=4,  choices=SIDE_CHOICES,   default="BUY")
    account = models.CharField(max_length=10, choices=ACCOUNT_CHOICES, default="SPEC")

    # ★ 追加: オープン日（保有日数を計算する基準。未設定なら created_at を使用）
    opened_at  = models.DateField(null=True, blank=True)
    memo = models.TextField(blank=True, default="")
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self):
        return f"{self.ticker} x{self.quantity}"

    def acquisition_value(self):
        """取得額 = quantity * avg_cost"""
        try:
            return (self.quantity or 0) * (self.avg_cost or 0)
        except Exception:
            return 0
            
    
# ==== RealizedTrade ======================================================
class RealizedTrade(models.Model):
    BROKER_CHOICES = (
        ("RAKUTEN", "楽天証券"),
        ("SBI",     "SBI証券"),
        ("MATSUI",  "松井証券"),
        ("OTHER",   "その他"),
    )
    ACCOUNT_CHOICES = (
        ("SPEC", "特定"),
        ("MARGIN", "信用"),
        ("NISA", "NISA"),
    )

    user      = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    trade_at  = models.DateField()
    side      = models.CharField(max_length=4, choices=(("SELL","SELL"),("BUY","BUY")))
    ticker    = models.CharField(max_length=20)
    name      = models.CharField(max_length=120, blank=True, default="")
    qty       = models.IntegerField()
    price     = models.DecimalField(max_digits=14, decimal_places=2)
    basis     = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    fee       = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    tax       = models.DecimalField(max_digits=14, decimal_places=2, default=0)

    broker    = models.CharField(max_length=16, choices=BROKER_CHOICES, default="OTHER")
    account   = models.CharField(max_length=10, choices=ACCOUNT_CHOICES, default="SPEC",
                                 help_text="口座区分（特定/信用/NISA）")
    cashflow  = models.DecimalField(
        max_digits=16, decimal_places=2, null=True, blank=True,
        help_text="受渡金額（現金フロー）。SELL=＋/BUY=−。未入力なら自動推定。"
    )

    # ★ 追加: 保有日数（クローズ時に自動保存）
    hold_days = models.IntegerField(null=True, blank=True, help_text="保有日数（未入力は平均集計から除外）")

    memo      = models.TextField(blank=True, default="")
    created_at= models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-trade_at","-id"]

    @property
    def amount(self):
        return float(self.qty) * float(self.price)

    @property
    def pnl(self):
        if self.side == "BUY":
            gross = 0.0
        else:
            b = float(self.basis) if self.basis is not None else float(self.price)
            gross = (float(self.price) - b) * float(self.qty)
        return gross - float(self.fee) - float(self.tax)

    @property
    def cashflow_effective(self):
        if self.cashflow is not None:
            return float(self.cashflow)
        signed = self.amount if self.side == "SELL" else -self.amount
        return signed - float(self.fee) - float(self.tax)
        
# ==== Dividend ======================================================
class Dividend(models.Model):
    """
    配当（Holdingが無くても記録可）
    - holding を指定したら、ticker/name は自動補完（空の場合）
    - holding 未指定の場合は ticker を必須としてバリデーション
    """
    holding = models.ForeignKey(
        'portfolio.Holding',
        on_delete=models.SET_NULL,   # 保有を消しても配当は残す
        null=True, blank=True,
        related_name='dividends'
    )

    # Holdingが無いとき用の手入力フィールド（あってもOK）
    ticker = models.CharField(max_length=16, blank=True, default="")
    name   = models.CharField(max_length=128, blank=True, default="")

    date   = models.DateField()

    # 受取額（フォームでは税引後入力がデフォルト）
    amount = models.DecimalField(max_digits=12, decimal_places=2)

    # True=税引後として入力、False=税引前入力（今回は常に True 推奨）
    is_net = models.BooleanField(default=True)

    # 源泉税（自動計算して保存：0 or 20.315% ベース）
    tax    = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)

    memo   = models.CharField(max_length=255, blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-date", "-id")

    def __str__(self):
        label = self.display_ticker or "—"
        return f"{label} {self.date} {self.amount}"

    # 表示用（holding優先）
    @property
    def display_ticker(self) -> str:
        if self.holding and self.holding.ticker:
            return self.holding.ticker
        return (self.ticker or "").upper()

    @property
    def display_name(self) -> str:
        if self.holding and self.holding.name:
            return self.holding.name
        return self.name or ""

    # 税引前/税引後（計算）
    def gross_amount(self):
        # 税引前
        if self.is_net:
            return (self.amount or 0) + (self.tax or 0)
        return self.amount or 0

    def net_amount(self):
        # 税引後
        if self.is_net:
            return self.amount or 0
        return (self.amount or 0) - (self.tax or 0)

    def save(self, *args, **kwargs):
        # holdingがあればticker/nameを自動補完（空のとき）
        if self.holding:
            if not self.ticker:
                self.ticker = self.holding.ticker
            if not self.name:
                self.name = self.holding.name
        super().save(*args, **kwargs)
