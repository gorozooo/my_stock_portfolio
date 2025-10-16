from django.conf import settings
from django.db import models
from django.contrib.auth import get_user_model
from decimal import Decimal

User = get_user_model()


class UserSetting(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    account_equity = models.BigIntegerField("口座残高(円)", default=1_000_000)
    risk_pct = models.FloatField("1トレードのリスク％", default=1.0)

    def __str__(self):
        return f"{self.user.username} 設定"


# ==== Holding ============================================================
class Holding(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)

    ticker = models.CharField(max_length=16)
    name   = models.CharField(max_length=128, blank=True)
    sector = models.CharField(max_length=64, blank=True, default="")  # ★追加：セクター33業種

    quantity = models.IntegerField(default=0)
    avg_cost = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    
    last_price = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True,
        help_text="最終終値（1株・自動更新）"
    )
    last_price_updated = models.DateTimeField(null=True, blank=True)
    
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

    opened_at  = models.DateField(null=True, blank=True)
    memo = models.TextField(blank=True, default="")
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self):
        return f"{self.ticker} x{self.quantity}"
            

# ==== RealizedTrade ======================================================
class RealizedTrade(models.Model):
    BROKER_CHOICES = (
        ("RAKUTEN", "楽天証券"),
        ("SBI",     "SBI証券"),
        ("MATSUI",  "松井証券"),
        ("OTHER",   "その他"),
    )
    ACCOUNT_CHOICES = (
        ("SPEC",   "特定"),
        ("MARGIN", "信用"),
        ("NISA",   "NISA"),
    )
    SIDE_CHOICES = (("SELL", "SELL"), ("BUY", "BUY"))

    user      = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    trade_at  = models.DateField(db_index=True)
    side      = models.CharField(max_length=4, choices=SIDE_CHOICES, db_index=True)
    ticker    = models.CharField(max_length=20, db_index=True)
    name      = models.CharField(max_length=120, blank=True, default="")
    qty       = models.IntegerField()
    price     = models.DecimalField(max_digits=14, decimal_places=2)
    basis     = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    fee       = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    tax       = models.DecimalField(max_digits=14, decimal_places=2, default=0)

    broker    = models.CharField(max_length=16, choices=BROKER_CHOICES, default="OTHER")
    account   = models.CharField(
        max_length=10,
        choices=ACCOUNT_CHOICES,
        default="SPEC",
        help_text="口座区分（特定/信用/NISA）"
    )

    cashflow  = models.DecimalField(
        max_digits=16, decimal_places=2, null=True, blank=True,
        help_text="受渡金額（現金フロー）。SELL=＋/BUY=−。未入力なら自動推定。"
    )

    # クローズ時に保存する保有日数（平均集計用）
    hold_days = models.IntegerField(null=True, blank=True, help_text="保有日数（未入力は平均集計から除外）")

    memo      = models.TextField(blank=True, default="")
    created_at= models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-trade_at", "-id"]
        indexes = [
            models.Index(fields=["trade_at", "side"]),
            models.Index(fields=["ticker", "trade_at"]),
        ]

    # --------- Helpers ---------
    @property
    def is_buy(self) -> bool:
        return (self.side or "").upper() == "BUY"

    @property
    def is_sell(self) -> bool:
        return (self.side or "").upper() == "SELL"

    @property
    def amount(self):
        """取引金額（qty * price）"""
        return float(self.qty) * float(self.price)

    @property
    def pnl(self):
        """
        手数料・税控除後の取引PnL（トレード起点）。
        BUYはオープン側なので0扱い、SELLのみ (price - basis) * qty - fee - tax。
        """
        if self.is_buy:
            gross = 0.0
        else:
            b = float(self.basis) if self.basis is not None else float(self.price)
            gross = (float(self.price) - b) * float(self.qty)
        return gross - float(self.fee) - float(self.tax)

    @property
    def cashflow_effective(self):
        """
        実際の現金増減（受渡ベース）。
        cashflow があればそれを優先。無ければ
          SELL: +(qty*price) - fee - tax
          BUY : -(qty*price) - fee - tax
        を自動算出。
        """
        if self.cashflow is not None:
            return float(self.cashflow)
        signed = self.amount if self.is_sell else -self.amount
        return signed - float(self.fee) - float(self.tax)

    # --------- Normalize / Defaults ---------
    def save(self, *args, **kwargs):
        """
        - BUY で basis 未入力なら、分析の整合性のため basis=price を自動補完
        - ティッカーは大文字に正規化
        """
        # 正規化
        if self.ticker:
            self.ticker = self.ticker.upper().strip()

        # BUY のとき basis を price で補完（None のままでも壊れないが指標計算が楽）
        if self.is_buy and self.basis is None:
            self.basis = self.price

        super().save(*args, **kwargs)
        

# ==== Dividend ======================================================
class Dividend(models.Model):
    """
    配当（Holding が無くても記録可）
    - holding を指定したら ticker/name/broker/account/purchase_price を不足分だけ補完
    - holding 未指定なら ticker は必須（バリデーションは Form 側で実施する前提）
    - KPI 用に数量・取得単価・証券会社・口座区分も保持
    """

    # ====== 参照 ======
    holding = models.ForeignKey(
        'portfolio.Holding',
        on_delete=models.SET_NULL,           # 保有を消しても配当は残す
        null=True, blank=True,
        related_name='dividends'
    )

    # ====== 基本情報（holding 無しでも記録できるように） ======
    ticker = models.CharField(max_length=16, blank=True, default="")
    name   = models.CharField(max_length=128, blank=True, default="")

    # 支払日（既存の date を Phase2 でも支払日として利用）
    date   = models.DateField()

    # --- Phase2: 予測・カレンダー強化用の日時/属性 ---
    ex_date     = models.DateField(null=True, blank=True, help_text="権利落ち日（任意）")
    record_date = models.DateField(null=True, blank=True, help_text="基準日（任意）")

    PERIOD_CHOICES = (
        ("FY",  "期末"),
        ("HY",  "中間"),
        ("Q",   "四半期"),
        ("UNK", "不明/その他"),
    )
    period = models.CharField(max_length=8, choices=PERIOD_CHOICES, default="UNK", blank=True)

    # 想定頻度のヒント（年1/2/4）
    FREQ_CHOICES = ((1, "年1"), (2, "年2"), (4, "年4"))
    freq_hint = models.PositiveSmallIntegerField(choices=FREQ_CHOICES, null=True, blank=True,
                                                 help_text="配当頻度の推定（任意）")

    # 数量（何株分の配当か）
    quantity = models.IntegerField(default=0, help_text="株数（KPI計算に使用）")

    # 取得単価（holding が無い場合に利回りを出すための単価）
    purchase_price = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True,
        help_text="1株あたりの取得単価（holding未指定時に利回り算出で使用）"
    )

    # ====== 金額（UIは税引後入力がデフォルト） ======
    amount = models.DecimalField(max_digits=12, decimal_places=2, help_text="受取額")
    is_net = models.BooleanField(default=True, help_text="True=税引後として入力 / False=税引前")

    # 税額／税率（保存しておくと集計が速い）
    tax            = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    tax_rate_pct   = models.DecimalField(
        max_digits=6, decimal_places=3, null=True, blank=True,
        help_text="適用税率（例 20.315）"
    )

    # ====== 区分（証券会社別KPI用） ======
    BROKER_CHOICES = (
        ("RAKUTEN", "楽天証券"),
        ("SBI",     "SBI証券"),
        ("MATSUI",  "松井証券"),
        ("OTHER",   "その他"),
    )
    ACCOUNT_CHOICES = (
        ("SPEC",   "特定"),
        ("MARGIN", "信用"),
        ("NISA",   "NISA"),
        ("OTHER",  "その他"),
    )

    broker  = models.CharField(max_length=16, choices=BROKER_CHOICES, default="OTHER")
    account = models.CharField(max_length=10, choices=ACCOUNT_CHOICES, default="SPEC")

    memo   = models.CharField(max_length=255, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-date", "-id")
        indexes = [
            models.Index(fields=["date"]),
            models.Index(fields=["broker"]),
            models.Index(fields=["account"]),
        ]

    def __str__(self):
        label = self.display_ticker or "—"
        return f"{label} {self.date} {self.amount}"

    # ---- 表示用（holding 優先） ----
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

    # alias: pay_date（カレンダー側の語彙に合わせたい時に使える）
    @property
    def pay_date(self):
        return self.date

    # ---- 金額：税引前/税引後 ----
    def gross_amount(self):
        """税引前金額"""
        try:
            amt = float(self.amount or 0)
            tx  = float(self.tax or 0)
            return amt + tx if self.is_net else amt
        except Exception:
            return 0.0

    def net_amount(self):
        """税引後金額"""
        try:
            amt = float(self.amount or 0)
            tx  = float(self.tax or 0)
            return amt if self.is_net else max(0.0, amt - tx)
        except Exception:
            return 0.0

    # ---- 利回り計算（KPI）----
    def _unit_cost(self):
        """
        単価の優先度:
        1) holding.avg_cost があればそれ
        2) purchase_price（手入力）
        """
        if self.holding and self.holding.avg_cost:
            return float(self.holding.avg_cost)
        if self.purchase_price:
            return float(self.purchase_price)
        return 0.0

    def acquisition_value(self):
        """取得額 = 単価 × 株数（利回りの分母）"""
        unit = self._unit_cost()
        qty  = int(self.quantity or 0)
        return unit * qty if unit > 0 and qty > 0 else 0.0

    def yoc_net_pct(self):
        """配当利回り（取得ベース・税引後%）"""
        base = self.acquisition_value()
        return (self.net_amount() / base * 100.0) if base > 0 else None

    def yoc_gross_pct(self):
        """配当利回り（取得ベース・税引前%）"""
        base = self.acquisition_value()
        return (self.gross_amount() / base * 100.0) if base > 0 else None

    def per_share_dividend_net(self):
        """1株あたり配当（税引後）"""
        qty = int(self.quantity or 0)
        return (self.net_amount() / qty) if qty > 0 else None

    def per_share_dividend_gross(self):
        """1株あたり配当（税引前）"""
        qty = int(self.quantity or 0)
        return (self.gross_amount() / qty) if qty > 0 else None

    # ---- 補完 & 整合性 ----
    def save(self, *args, **kwargs):
        # holding があれば不足分を補完
        if self.holding:
            if not self.ticker:
                self.ticker = self.holding.ticker
            if not self.name:
                self.name = self.holding.name
            # broker/account/purchase_price も穴埋め
            if (not self.broker or self.broker == "OTHER") and self.holding.broker:
                self.broker = self.holding.broker
            if (not self.account or self.account == "SPEC") and self.holding.account:
                self.account = self.holding.account
            if not self.purchase_price and self.holding.avg_cost:
                self.purchase_price = self.holding.avg_cost

        # 税率が入っていれば税額を補完（is_net=True 前提のUI）
        try:
            if (self.tax is None or float(self.tax) == 0.0) and self.tax_rate_pct:
                rate = float(self.tax_rate_pct) / 100.0
                if self.is_net:
                    # amount は税引後 → 税額 = net * rate
                    self.tax = float(self.amount or 0) * rate
                else:
                    # amount は税引前 → 税額 = gross * rate
                    self.tax = float(self.amount or 0) * rate
        except Exception:
            pass

        super().save(*args, **kwargs)


class DividendGoal(models.Model):
    user      = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, db_index=True)
    year      = models.IntegerField(db_index=True)
    amount    = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "year"], name="uniq_dividend_goal_user_year"),
        ]
        indexes = [models.Index(fields=["user", "year"])]

    def __str__(self):
        return f"{self.user} {self.year} → {self.amount}"