# [FILE] models.py
# [PATH] portfolio/models.py
#
# このファイルは何？
# - portfolio アプリの主要モデル定義
#
# 今回の方針
# - Holding は「現在保有」だけ
# - TradeEvent を追加して、買い/売り/信用返済/現引の原簿にする
# - RealizedTrade はクローズ履歴・分析用として残す
# - CashLedger は TradeEvent をソースに現金化する

from __future__ import annotations

from decimal import Decimal
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import models
from django.utils import timezone

from .models_market import *

User = get_user_model()


class UserSetting(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)

    account_equity = models.BigIntegerField("口座残高(円)", default=1_000_000)
    risk_pct = models.FloatField("1トレードのリスク％", default=1.0)

    credit_usage_pct = models.FloatField("信用余力の使用上限％", default=70.0)

    leverage_rakuten = models.FloatField("楽天 倍率", default=2.90)
    haircut_rakuten = models.FloatField("楽天 ヘアカット率", default=0.30)

    leverage_matsui = models.FloatField("松井 倍率", default=2.80)
    haircut_matsui = models.FloatField("松井 ヘアカット率", default=0.00)

    leverage_sbi = models.FloatField("SBI 倍率", default=2.80)
    haircut_sbi = models.FloatField("SBI ヘアカット率", default=0.00)

    year_goal_total = models.BigIntegerField("年間目標（全体・円）", default=0)
    year_goal_by_broker = models.JSONField("年間目標（証券会社別）", default=dict, blank=True)

    def __str__(self):
        return f"{self.user.username} 設定"


class Holding(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)

    ticker = models.CharField(max_length=16)
    name = models.CharField(max_length=128, blank=True)
    sector = models.CharField(max_length=64, blank=True, default="")

    MARKET_CHOICES = (
        ("JP", "日本株"),
        ("US", "米国株"),
    )
    CURRENCY_CHOICES = (
        ("JPY", "JPY"),
        ("USD", "USD"),
    )
    market = models.CharField(max_length=4, choices=MARKET_CHOICES, default="JP")
    currency = models.CharField(max_length=4, choices=CURRENCY_CHOICES, default="JPY")

    fx_rate = models.DecimalField(
        max_digits=12,
        decimal_places=6,
        null=True,
        blank=True,
        help_text="1通貨あたりの円レート（例: 155.250000）",
    )

    quantity = models.IntegerField(default=0)
    avg_cost = models.DecimalField(max_digits=14, decimal_places=2, default=0)

    last_price = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="最終終値（1株・自動更新）",
    )
    last_price_updated = models.DateTimeField(null=True, blank=True)

    BROKER_CHOICES = (
        ("RAKUTEN", "楽天証券"),
        ("SBI", "SBI証券"),
        ("MATSUI", "松井証券"),
       
    )
    SIDE_CHOICES = (("BUY", "BUY"), ("SELL", "SELL"))
    ACCOUNT_CHOICES = (
        ("SPEC", "特定"),
        ("MARGIN", "信用"),
        ("NISA", "NISA"),
    )

    broker = models.CharField(max_length=16, choices=BROKER_CHOICES, default="OTHER")
    side = models.CharField(max_length=4, choices=SIDE_CHOICES, default="BUY")
    account = models.CharField(max_length=10, choices=ACCOUNT_CHOICES, default="SPEC")

    opened_at = models.DateField(null=True, blank=True)
    memo = models.TextField(blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self):
        return f"{self.ticker} x{self.quantity}"


class RealizedTrade(models.Model):
    """
    クローズ履歴・分析用。
    - 売却 / 返済の分析・ランキング・月次集計はこちらを使う
    - 現金台帳の source-of-truth は TradeEvent に寄せる
    """

    BROKER_CHOICES = (
        ("RAKUTEN", "楽天証券"),
        ("SBI", "SBI証券"),
        ("MATSUI", "松井証券"),
        ("OTHER", "その他"),
    )
    ACCOUNT_CHOICES = (
        ("SPEC", "特定"),
        ("MARGIN", "信用"),
        ("NISA", "NISA"),
    )
    SIDE_CHOICES = (("SELL", "SELL"), ("BUY", "BUY"))

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)

    trade_at = models.DateField(db_index=True)
    opened_at = models.DateField(
        null=True,
        blank=True,
        help_text="このポジションの保有開始日（エントリー日）",
    )

    side = models.CharField(max_length=4, choices=SIDE_CHOICES, db_index=True)

    ticker = models.CharField(max_length=20, db_index=True)
    name = models.CharField(max_length=120, blank=True, default="")

    sector33_code = models.CharField(
        max_length=16,
        blank=True,
        default="",
        help_text="33業種コード",
    )
    sector33_name = models.CharField(
        max_length=64,
        blank=True,
        default="",
        help_text="33業種名",
    )

    qty = models.IntegerField()
    price = models.DecimalField(max_digits=14, decimal_places=2)
    basis = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    fee = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    tax = models.DecimalField(max_digits=14, decimal_places=2, default=0)

    broker = models.CharField(max_length=16, choices=BROKER_CHOICES, default="OTHER")
    account = models.CharField(
        max_length=10,
        choices=ACCOUNT_CHOICES,
        default="SPEC",
        help_text="口座区分（特定/信用/NISA）",
    )

    country = models.CharField(
        max_length=8,
        blank=True,
        default="JP",
        help_text="上場国コード（JP / US など）",
    )
    currency = models.CharField(
        max_length=8,
        blank=True,
        default="JPY",
        help_text="取引通貨（JPY, USD など）",
    )

    fx_rate = models.DecimalField(
        max_digits=12,
        decimal_places=6,
        null=True,
        blank=True,
        help_text="互換用FX。基本はクローズ時FXとして扱う",
    )
    open_fx_rate = models.DecimalField(
        max_digits=12,
        decimal_places=6,
        null=True,
        blank=True,
        help_text="オープン時の為替レート",
    )
    close_fx_rate = models.DecimalField(
        max_digits=12,
        decimal_places=6,
        null=True,
        blank=True,
        help_text="クローズ時の為替レート",
    )

    cashflow = models.DecimalField(
        max_digits=16,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="表示互換用。現金台帳の元データには使わない",
    )

    hold_days = models.IntegerField(null=True, blank=True, help_text="保有日数")

    strategy_label = models.CharField(max_length=64, blank=True, default="")
    policy_key = models.CharField(max_length=64, blank=True, default="")
    is_ai_signal = models.BooleanField(default=False)
    position_key = models.CharField(max_length=64, blank=True, default="")

    memo = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-trade_at", "-id"]
        indexes = [
            models.Index(fields=["trade_at", "side"]),
            models.Index(fields=["ticker", "trade_at"]),
            models.Index(fields=["sector33_code", "trade_at"]),
            models.Index(fields=["country", "trade_at"]),
        ]

    @property
    def is_buy(self) -> bool:
        return (self.side or "").upper() == "BUY"

    @property
    def is_sell(self) -> bool:
        return (self.side or "").upper() == "SELL"

    @property
    def amount(self):
        return float(self.qty) * float(self.price)

    @property
    def pnl(self):
        if self.is_buy:
            gross = 0.0
        else:
            b = float(self.basis) if self.basis is not None else float(self.price)
            gross = (float(self.price) - b) * float(self.qty)
        return gross - float(self.fee) - float(self.tax)

    @property
    def cashflow_effective(self):
        if self.cashflow is not None:
            return float(self.cashflow)
        signed = self.amount if self.is_sell else -self.amount
        return signed - float(self.fee) - float(self.tax)

    def _fx_open(self) -> float:
        if self.open_fx_rate:
            return float(self.open_fx_rate)
        if self.fx_rate:
            return float(self.fx_rate)
        return 1.0

    def _fx_close(self) -> float:
        if self.close_fx_rate:
            return float(self.close_fx_rate)
        if self.fx_rate:
            return float(self.fx_rate)
        return 1.0

    @property
    def pnl_jpy(self):
        cur = (self.currency or "").upper()
        if cur == "JPY":
            return self.pnl

        if self.basis is not None:
            try:
                open_fx = self._fx_open()
                close_fx = self._fx_close()
                if self.is_sell:
                    yen_close = float(self.price) * float(self.qty) * close_fx
                    yen_open = float(self.basis) * float(self.qty) * open_fx
                    yen_fee_tax = (float(self.fee) + float(self.tax)) * close_fx
                    return (yen_close - yen_open) - yen_fee_tax
                else:
                    yen_open = float(self.price) * float(self.qty) * open_fx
                    yen_close = float(self.basis) * float(self.qty) * close_fx
                    yen_fee_tax = (float(self.fee) + float(self.tax)) * close_fx
                    return (yen_close - yen_open) - yen_fee_tax
            except Exception:
                pass

        try:
            return float(self.pnl) * float(self._fx_close())
        except Exception:
            return self.pnl

    @property
    def cashflow_effective_jpy(self):
        cur = (self.currency or "").upper()
        cf = self.cashflow_effective
        if cur == "JPY":
            return cf
        try:
            fx = self._fx_close() if self.is_sell else self._fx_open()
            return float(cf) * float(fx)
        except Exception:
            if self.fx_rate:
                return float(cf) * float(self.fx_rate)
            return cf

    def save(self, *args, **kwargs):
        if self.ticker:
            self.ticker = self.ticker.upper().strip()

        if self.is_buy and self.basis is None:
            self.basis = self.price

        if not self.country:
            self.country = "JP"
        if not self.currency:
            self.currency = "JPY"

        if self.fx_rate and not self.close_fx_rate:
            self.close_fx_rate = self.fx_rate

        super().save(*args, **kwargs)


class TradeEvent(models.Model):
    """
    売買イベントの原簿。
    ここから CashLedger を組み立てる。
    """

    class EventType(models.TextChoices):
        SPOT_BUY = "SPOT_BUY", "現物買い"
        SPOT_SELL = "SPOT_SELL", "現物売り"
        MARGIN_OPEN = "MARGIN_OPEN", "信用新規"
        MARGIN_CLOSE = "MARGIN_CLOSE", "信用返済"
        MARGIN_TO_SPOT = "MARGIN_TO_SPOT", "現引"

    BROKER_CHOICES = RealizedTrade.BROKER_CHOICES
    ACCOUNT_CHOICES = RealizedTrade.ACCOUNT_CHOICES
    SIDE_CHOICES = RealizedTrade.SIDE_CHOICES

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    holding = models.ForeignKey(
        "portfolio.Holding",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="trade_events",
    )
    realized_trade = models.ForeignKey(
        "portfolio.RealizedTrade",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="trade_events",
    )

    trade_at = models.DateField(db_index=True)
    opened_at = models.DateField(null=True, blank=True)

    event_type = models.CharField(max_length=20, choices=EventType.choices, db_index=True)
    side = models.CharField(max_length=4, choices=SIDE_CHOICES, db_index=True)

    ticker = models.CharField(max_length=20, db_index=True)
    name = models.CharField(max_length=120, blank=True, default="")
    sector33_code = models.CharField(max_length=16, blank=True, default="")
    sector33_name = models.CharField(max_length=64, blank=True, default="")

    qty = models.IntegerField()
    price = models.DecimalField(max_digits=14, decimal_places=2)
    basis = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    fee = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    tax = models.DecimalField(max_digits=14, decimal_places=2, default=0)

    broker = models.CharField(max_length=16, choices=BROKER_CHOICES, default="OTHER")
    account = models.CharField(max_length=10, choices=ACCOUNT_CHOICES, default="SPEC")

    country = models.CharField(max_length=8, blank=True, default="JP")
    currency = models.CharField(max_length=8, blank=True, default="JPY")

    fx_rate = models.DecimalField(max_digits=12, decimal_places=6, null=True, blank=True)
    open_fx_rate = models.DecimalField(max_digits=12, decimal_places=6, null=True, blank=True)
    close_fx_rate = models.DecimalField(max_digits=12, decimal_places=6, null=True, blank=True)

    cash_amount_jpy = models.BigIntegerField(
        null=True,
        blank=True,
        help_text="このイベントが現金に与えるJPY増減。＋入金 / −出金",
    )

    memo = models.TextField(blank=True, default="")
    position_key = models.CharField(max_length=64, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-trade_at", "-id"]
        indexes = [
            models.Index(fields=["trade_at", "event_type"]),
            models.Index(fields=["ticker", "trade_at"]),
            models.Index(fields=["broker", "trade_at"]),
            models.Index(fields=["position_key"]),
        ]

    @property
    def is_buy(self) -> bool:
        return (self.side or "").upper() == "BUY"

    @property
    def is_sell(self) -> bool:
        return (self.side or "").upper() == "SELL"

    @property
    def amount(self) -> float:
        return float(self.qty or 0) * float(self.price or 0)

    def _fx_open(self) -> float:
        if self.open_fx_rate:
            return float(self.open_fx_rate)
        if self.fx_rate:
            return float(self.fx_rate)
        return 1.0

    def _fx_close(self) -> float:
        if self.close_fx_rate:
            return float(self.close_fx_rate)
        if self.fx_rate:
            return float(self.fx_rate)
        return 1.0

    def settlement_amount_native(self) -> float:
        gross = float(self.qty or 0) * float(self.price or 0)
        fee = float(self.fee or 0)
        tax = float(self.tax or 0)
        return gross - fee - tax if self.is_sell else -(gross + fee + tax)

    def realized_pnl_jpy(self) -> int:
        if self.basis is None:
            return 0

        qty = float(self.qty or 0)
        fee = float(self.fee or 0)
        tax = float(self.tax or 0)

        cur = (self.currency or "").upper()
        if cur == "JPY":
            if self.is_sell:
                pnl = (float(self.price) - float(self.basis)) * qty - fee - tax
            else:
                pnl = (float(self.basis) - float(self.price)) * qty - fee - tax
            return int(round(pnl))

        open_fx = self._fx_open()
        close_fx = self._fx_close()

        if self.is_sell:
            yen_close = float(self.price) * qty * close_fx
            yen_open = float(self.basis) * qty * open_fx
            pnl = (yen_close - yen_open) - ((fee + tax) * close_fx)
        else:
            yen_open = float(self.price) * qty * open_fx
            yen_close = float(self.basis) * qty * close_fx
            pnl = (yen_close - yen_open) - ((fee + tax) * close_fx)

        return int(round(pnl))

    def compute_cash_amount_jpy(self) -> int:
        if self.event_type == self.EventType.MARGIN_OPEN:
            return 0

        if self.event_type == self.EventType.MARGIN_CLOSE:
            return self.realized_pnl_jpy()

        if self.event_type == self.EventType.MARGIN_TO_SPOT:
            base = float(self.basis if self.basis is not None else self.price)
            qty = float(self.qty or 0)
            fee = float(self.fee or 0)
            tax = float(self.tax or 0)
            native = -((base * qty) + fee + tax)
            if (self.currency or "").upper() == "JPY":
                return int(round(native))
            return int(round(native * self._fx_open()))

        native = self.settlement_amount_native()
        if (self.currency or "").upper() == "JPY":
            return int(round(native))

        fx = self._fx_close() if self.is_sell else self._fx_open()
        return int(round(native * fx))

    def save(self, *args, **kwargs):
        if self.ticker:
            self.ticker = self.ticker.upper().strip()

        if not self.country:
            self.country = "JP"
        if not self.currency:
            self.currency = "JPY"

        if self.fx_rate and not self.close_fx_rate:
            self.close_fx_rate = self.fx_rate

        if self.cash_amount_jpy is None:
            self.cash_amount_jpy = self.compute_cash_amount_jpy()

        super().save(*args, **kwargs)


class Dividend(models.Model):
    holding = models.ForeignKey(
        "portfolio.Holding",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="dividends",
    )

    ticker = models.CharField(max_length=16, blank=True, default="")
    name = models.CharField(max_length=128, blank=True, default="")
    date = models.DateField()

    ex_date = models.DateField(null=True, blank=True, help_text="権利落ち日（任意）")
    record_date = models.DateField(null=True, blank=True, help_text="基準日（任意）")

    PERIOD_CHOICES = (
        ("FY", "期末"),
        ("HY", "中間"),
        ("Q", "四半期"),
        ("UNK", "不明/その他"),
    )
    period = models.CharField(max_length=8, choices=PERIOD_CHOICES, default="UNK", blank=True)

    FREQ_CHOICES = ((1, "年1"), (2, "年2"), (4, "年4"))
    freq_hint = models.PositiveSmallIntegerField(choices=FREQ_CHOICES, null=True, blank=True)

    quantity = models.IntegerField(default=0)
    purchase_price = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="1株あたりの取得単価",
    )

    amount = models.DecimalField(max_digits=12, decimal_places=2, help_text="受取額")
    is_net = models.BooleanField(default=True, help_text="True=税引後入力 / False=税引前入力")

    tax = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    tax_rate_pct = models.DecimalField(max_digits=6, decimal_places=3, null=True, blank=True)

    BROKER_CHOICES = (
        ("RAKUTEN", "楽天証券"),
        ("SBI", "SBI証券"),
        ("MATSUI", "松井証券"),
        ("OTHER", "その他"),
    )
    ACCOUNT_CHOICES = (
        ("SPEC", "特定"),
        ("MARGIN", "信用"),
        ("NISA", "NISA"),
        ("OTHER", "その他"),
    )

    broker = models.CharField(max_length=16, choices=BROKER_CHOICES, default="OTHER")
    account = models.CharField(max_length=10, choices=ACCOUNT_CHOICES, default="SPEC")

    memo = models.CharField(max_length=255, blank=True, default="")
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

    @property
    def pay_date(self):
        return self.date

    def gross_amount(self):
        try:
            amt = float(self.amount or 0)
            tx = float(self.tax or 0)
            return amt + tx if self.is_net else amt
        except Exception:
            return 0.0

    def net_amount(self):
        try:
            amt = float(self.amount or 0)
            tx = float(self.tax or 0)
            return amt if self.is_net else max(0.0, amt - tx)
        except Exception:
            return 0.0

    def _unit_cost(self):
        if self.holding and self.holding.avg_cost:
            return float(self.holding.avg_cost)
        if self.purchase_price:
            return float(self.purchase_price)
        return 0.0

    def acquisition_value(self):
        unit = self._unit_cost()
        qty = int(self.quantity or 0)
        return unit * qty if unit > 0 and qty > 0 else 0.0

    def yoc_net_pct(self):
        base = self.acquisition_value()
        return (self.net_amount() / base * 100.0) if base > 0 else None

    def yoc_gross_pct(self):
        base = self.acquisition_value()
        return (self.gross_amount() / base * 100.0) if base > 0 else None

    def per_share_dividend_net(self):
        qty = int(self.quantity or 0)
        return (self.net_amount() / qty) if qty > 0 else None

    def per_share_dividend_gross(self):
        qty = int(self.quantity or 0)
        return (self.gross_amount() / qty) if qty > 0 else None

    def save(self, *args, **kwargs):
        if self.holding:
            if not self.ticker:
                self.ticker = self.holding.ticker
            if not self.name:
                self.name = self.holding.name
            if (not self.broker or self.broker == "OTHER") and self.holding.broker:
                self.broker = self.holding.broker
            if (not self.account or self.account == "SPEC") and self.holding.account:
                self.account = self.holding.account
            if not self.purchase_price and self.holding.avg_cost:
                self.purchase_price = self.holding.avg_cost

        try:
            if (self.tax is None or float(self.tax) == 0.0) and self.tax_rate_pct:
                rate = float(self.tax_rate_pct) / 100.0
                self.tax = float(self.amount or 0) * rate
        except Exception:
            pass

        super().save(*args, **kwargs)


class DividendGoal(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, db_index=True)
    year = models.IntegerField(db_index=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "year"], name="uniq_dividend_goal_user_year"),
        ]
        indexes = [models.Index(fields=["user", "year"])]

    def __str__(self):
        return f"{self.user} {self.year} → {self.amount}"


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