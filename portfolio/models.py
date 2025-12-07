from django.conf import settings
from django.db import models
from django.contrib.auth import get_user_model
from decimal import Decimal

from .models_market import *

User = get_user_model()


# =============================
# ユーザー設定（AIの数量計算・倍率/ヘアカット率など）
# =============================
class UserSetting(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)

    # 旧：口座残高＆リスク％（既存）
    account_equity = models.BigIntegerField("口座残高(円)", default=1_000_000)
    risk_pct = models.FloatField("1トレードのリスク％", default=1.0)

    # 追加：信用余力の使用上限（％）
    # 例: 70.0 なら「信用余力の 70% までを数量計算に使う」
    credit_usage_pct = models.FloatField("信用余力の使用上限％", default=70.0)

    # 追加：証券会社ごとの倍率/ヘアカット率（既定はあなたの運用に合わせて設定）
    leverage_rakuten = models.FloatField("楽天 倍率", default=2.90)
    haircut_rakuten  = models.FloatField("楽天 ヘアカット率", default=0.30)  # 30%

    leverage_matsui  = models.FloatField("松井 倍率", default=2.80)
    haircut_matsui   = models.FloatField("松井 ヘアカット率", default=0.00)
    
    leverage_sbi  = models.FloatField("SBI 倍率", default=2.80)
    haircut_sbi   = models.FloatField("SBI ヘアカット率", default=0.00)

    def __str__(self):
        return f"{self.user.username} 設定"


# ==== Holding ============================================================
class Holding(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)

    # === 銘柄基本情報 ===
    ticker = models.CharField(max_length=16)
    name   = models.CharField(max_length=128, blank=True)
    sector = models.CharField(max_length=64, blank=True, default="")  # 33業種

    # === 市場・通貨（★追加済） ===
    MARKET_CHOICES = (
        ("JP", "日本株"),
        ("US", "米国株"),
    )
    CURRENCY_CHOICES = (
        ("JPY", "JPY"),
        ("USD", "USD"),
    )
    market   = models.CharField(max_length=4, choices=MARKET_CHOICES, default="JP")
    currency = models.CharField(max_length=4, choices=CURRENCY_CHOICES, default="JPY")

    # ★ ここを追加：取得時の為替レート（証券会社の約定レートをそのまま入れる）
    fx_rate = models.DecimalField(
        max_digits=12,
        decimal_places=6,
        null=True,
        blank=True,
        help_text="1通貨あたりの円レート（例: 155.250000）"
    )

    # === 保有データ ===
    quantity = models.IntegerField(default=0)
    avg_cost = models.DecimalField(max_digits=14, decimal_places=2, default=0)

    last_price = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True,
        help_text="最終終値（1株・自動更新）"
    )
    last_price_updated = models.DateTimeField(null=True, blank=True)

    # === 口座・属性 ===
    BROKER_CHOICES = (
        ("RAKUTEN", "楽天証券"),
        ("SBI",     "SBI証券"),
        ("MATSUI",  "松井証券"),
        ("OTHER",   "その他"),
    )
    SIDE_CHOICES = (("BUY", "BUY"), ("SELL", "SELL"))
    ACCOUNT_CHOICES = (
        ("SPEC", "特定"),
        ("MARGIN", "信用"),
        ("NISA", "NISA"),
    )

    broker  = models.CharField(max_length=16, choices=BROKER_CHOICES, default="OTHER")
    side    = models.CharField(max_length=4,  choices=SIDE_CHOICES,   default="BUY")
    account = models.CharField(max_length=10, choices=ACCOUNT_CHOICES, default="SPEC")

    # === 日付系 ===
    opened_at  = models.DateField(null=True, blank=True)

    # === メモ ===
    memo = models.TextField(blank=True, default="")

    # === タイムスタンプ ===
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

    # 取引日（クローズ日）
    trade_at  = models.DateField(db_index=True)

    # 🔸 新規：保有開始日（エントリー日）
    opened_at = models.DateField(
        null=True, blank=True,
        help_text="このポジションの保有開始日（エントリー日）"
    )

    side      = models.CharField(max_length=4, choices=SIDE_CHOICES, db_index=True)

    # ティッカー / 銘柄名
    ticker    = models.CharField(max_length=20, db_index=True)
    name      = models.CharField(max_length=120, blank=True, default="")

    # 🔸 新規：33業種（コード＋名前）
    sector33_code = models.CharField(
        max_length=8,
        blank=True,
        default="",
        help_text="33業種コード（例: 6050）"
    )
    sector33_name = models.CharField(
        max_length=64,
        blank=True,
        default="",
        help_text="33業種名（例: 情報・通信業）"
    )

    qty       = models.IntegerField()
    price     = models.DecimalField(max_digits=14, decimal_places=2)
    basis     = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    fee       = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    tax       = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    sector33_code = models.CharField(max_length=16, blank=True, default="")
    
    broker    = models.CharField(max_length=16, choices=BROKER_CHOICES, default="OTHER")
    account   = models.CharField(
        max_length=10,
        choices=ACCOUNT_CHOICES,
        default="SPEC",
        help_text="口座区分（特定/信用/NISA）"
    )

    # 🔸 新規：国・通貨・為替
    country = models.CharField(
        max_length=8,
        blank=True,
        default="JP",
        help_text="上場国コード（JP / US など）"
    )
    currency = models.CharField(
        max_length=8,
        blank=True,
        default="JPY",
        help_text="取引通貨（JPY, USD など）"
    )
    fx_rate = models.DecimalField(
        max_digits=12, decimal_places=6,
        null=True, blank=True,
        help_text="基準通貨(JPY)への為替レート。1通貨あたり何円か（例: 1USD=150.250000）"
    )

    cashflow  = models.DecimalField(
        max_digits=16, decimal_places=2, null=True, blank=True,
        help_text="受渡金額（現金フロー）。SELL=＋/BUY=−。未入力なら自動推定。"
    )

    # クローズ時に保存する保有日数（平均集計用）
    hold_days = models.IntegerField(null=True, blank=True, help_text="保有日数（未入力は平均集計から除外）")

    # 🔸 新規：戦略 / ポリシー / AIフラグ / ポジションキー
    strategy_label = models.CharField(
        max_length=64,
        blank=True,
        default="",
        help_text="手動入力用のざっくり戦略ラベル（例: スイング, デイトレ, NISA長期など）"
    )
    policy_key = models.CharField(
        max_length=64,
        blank=True,
        default="",
        help_text="AdvisorPolicy等と紐づけるためのキー（例: core_v1, swing_breakout_v2 など）"
    )
    is_ai_signal = models.BooleanField(
        default=False,
        help_text="AIアドバイザーのシグナルに基づくトレードかどうか"
    )
    position_key = models.CharField(
        max_length=64,
        blank=True,
        default="",
        help_text="同一ポジション（分割エントリー・分割決済）を識別するためのキー"
    )

    memo      = models.TextField(blank=True, default="")
    created_at= models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-trade_at", "-id"]
        indexes = [
            models.Index(fields=["trade_at", "side"]),
            models.Index(fields=["ticker", "trade_at"]),
            # 🔸 将来の集計用に軽くインデックス追加（任意）
            models.Index(fields=["sector33_code", "trade_at"]),
            models.Index(fields=["country", "trade_at"]),
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

    # 🔸 追加：JPY換算PnL（US株で使える・DBには保存しない）
    @property
    def pnl_jpy(self):
        """
        通貨がJPY以外で fx_rate があれば、JPY換算したPnL。
        なければ通常の pnl をそのまま返す。
        """
        if (self.currency or "").upper() == "JPY" or not self.fx_rate:
            return self.pnl
        return float(self.pnl) * float(self.fx_rate)

    @property
    def cashflow_effective_jpy(self):
        """
        通貨がJPY以外で fx_rate があれば、JPY換算した実現キャッシュフロー。
        """
        cf = self.cashflow_effective
        if (self.currency or "").upper() == "JPY" or not self.fx_rate:
            return cf
        return float(cf) * float(self.fx_rate)

    # --------- Normalize / Defaults ---------
    def save(self, *args, **kwargs):
        """
        - BUY で basis 未入力なら、分析の整合性のため basis=price を自動補完
        - ティッカーは大文字に正規化
        - country / currency のデフォルト補正
        """
        # 正規化
        if self.ticker:
            self.ticker = self.ticker.upper().strip()

        # BUY のとき basis を price で補完（None のままでも壊れないが指標計算が楽）
        if self.is_buy and self.basis is None:
            self.basis = self.price

        # 国 / 通貨が空ならデフォルト補完
        if not self.country:
            self.country = "JP"
        if not self.currency:
            self.currency = "JPY"

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
        
# =============================
# ポジション管理（信用トレード専用）
# =============================
class Position(models.Model):
    SIDE_CHOICES = [
        ("LONG", "買い"),
        ("SHORT", "売り"),
    ]
    STATE_CHOICES = [
        ("OPEN", "保有中"),
        ("CLOSED", "完了"),
    ]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    ticker = models.CharField("証券コード", max_length=10)
    name = models.CharField("銘柄名", max_length=100, blank=True, default="")
    side = models.CharField("売買方向", max_length=5, choices=SIDE_CHOICES)
    entry_price = models.FloatField("エントリー価格")
    stop_price = models.FloatField("ストップ価格")
    qty = models.PositiveIntegerField("数量")
    targets = models.JSONField("利確ターゲット", default=list, blank=True)
    opened_at = models.DateTimeField("建玉日時", auto_now_add=True)
    closed_at = models.DateTimeField("クローズ日時", null=True, blank=True)
    state = models.CharField("状態", max_length=10, choices=STATE_CHOICES, default="OPEN")
    pnl_yen = models.FloatField("損益額", null=True, blank=True)
    pnl_R = models.FloatField("損益R", null=True, blank=True)
    max_MFE_R = models.FloatField("最大有利変動R", null=True, blank=True)
    max_MAE_R = models.FloatField("最大不利変動R", null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["ticker", "state"])]
        ordering = ["-opened_at"]

    def __str__(self):
        return f"{self.ticker} ({self.side}) {self.state}"
        