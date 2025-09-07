from django.db import models
from django.utils import timezone
from django.core.exceptions import ValidationError
import re
from django.conf import settings

# =============================
# 株マスター（証券コード・銘柄・33業種）
# =============================
class StockMaster(models.Model):
    code = models.CharField("証券コード", max_length=4, unique=True, db_index=True)
    name = models.CharField("銘柄名", max_length=200)
    sector = models.CharField("33業種", max_length=100, blank=True)

    def __str__(self):
        return f"{self.code} {self.name}"

# portfolio/models.py の一部（Stockモデル）
import re
import datetime
import yfinance as yf

from django.db import models
from django.core.exceptions import ValidationError
from django.utils import timezone


# portfolio/models.py
import re
import yfinance as yf
from django.db import models
from django.core.exceptions import ValidationError
from django.utils import timezone


class Stock(models.Model):
    # 証券会社
    BROKER_CHOICES = [
        ("楽天", "楽天"),
        ("松井", "松井"),
        ("moomoo", "moomoo"),
        ("SBI", "SBI"),
    ]

    # 口座区分
    ACCOUNT_TYPE_CHOICES = [
        ("現物", "現物"),
        ("信用", "信用"),
        ("NISA",  "NISA"),
    ]

    # ポジション
    POSITION_CHOICES = [
        ("買い", "買い"),
        ("売り", "売り"),
    ]

    purchase_date = models.DateField("購入日")
    ticker        = models.CharField("証券コード", max_length=20, db_index=True)  # 例: 7203 / 7203.T
    name          = models.CharField("銘柄名", max_length=100)
    account_type  = models.CharField("口座区分", max_length=10, choices=ACCOUNT_TYPE_CHOICES, default="現物", db_index=True)
    sector        = models.CharField("セクター", max_length=50, default="")
    position      = models.CharField("ポジション", max_length=4, choices=POSITION_CHOICES, default="買い", db_index=True)

    shares     = models.PositiveIntegerField("株数")
    unit_price = models.FloatField("取得単価")

    total_cost    = models.PositiveIntegerField("取得額", editable=False)  # shares * unit_price を四捨五入
    current_price = models.FloatField("現在株価", default=0, editable=False)
    market_value  = models.FloatField("評価額",   default=0, editable=False)
    profit_loss   = models.FloatField("損益額",   default=0, editable=False)

    broker     = models.CharField("証券会社", max_length=20, choices=BROKER_CHOICES, default="楽天", db_index=True)
    note       = models.TextField("メモ", blank=True, default="")
    created_at = models.DateTimeField("作成日時", default=timezone.now)
    updated_at = models.DateTimeField("更新日時", auto_now=True)

    class Meta:
        ordering = ["-purchase_date", "-created_at"]
        verbose_name = "保有株"
        verbose_name_plural = "保有株"
        indexes = [
            models.Index(fields=["broker", "account_type", "name"]),
            models.Index(fields=["ticker"]),
        ]

    # ---------- 正規化ヘルパ ----------
    @staticmethod
    def to_yf_symbol(ticker: str) -> str:
        """4桁数字のみなら東証銘柄として '.T' を付与。すでに拡張子ありならそのまま。"""
        t = (ticker or "").strip()
        if not t:
            return t
        if "." in t:
            return t
        if re.fullmatch(r"\d{4}", t):
            return f"{t}.T"
        return t

    @staticmethod
    def normalize_position(value: str) -> str:
        """'買'/'買い' → '買い'、'売'/'売り' → '売り' に統一。"""
        v = (value or "").strip()
        if v in ("買", "買い"):
            return "買い"
        if v in ("売", "売り"):
            return "売り"
        return v

    # ---------- バリデーション ----------
    def clean(self):
        super().clean()

        # 未来日禁止
        if self.purchase_date and self.purchase_date > timezone.localdate():
            raise ValidationError({"purchase_date": "購入日に未来日は指定できません。"})

        # ポジション正規化と検証
        pos = self.normalize_position(self.position)
        if pos not in dict(self.POSITION_CHOICES):
            raise ValidationError({"position": "ポジションは『買い』または『売り』を選択してください。"})
        self.position = pos

        # 口座区分検証（保険）
        if self.account_type not in dict(self.ACCOUNT_TYPE_CHOICES):
            raise ValidationError({"account_type": "口座区分の値が不正です。"})

    # ---------- 保存時の自動計算 ----------
    def save(self, *args, **kwargs):
        # 取得額（整数円）
        try:
            self.total_cost = int(round(float(self.shares) * float(self.unit_price)))
        except Exception:
            self.total_cost = 0

        # 現在値の更新（失敗時は既存値を保持）
        try:
            symbol = self.to_yf_symbol(self.ticker)
            if symbol:
                price_series = yf.Ticker(symbol).history(period="1d")["Close"]
                if len(price_series) > 0:
                    self.current_price = float(price_series.iloc[-1])
        except Exception:
            pass

        # 評価額
        try:
            self.market_value = float(self.shares) * float(self.current_price)
        except Exception:
            self.market_value = 0.0

        # 損益（買い: (現値×株数) - 取得額 / 売り: (取得単価 - 現値)×株数）
        pos = self.normalize_position(self.position)
        try:
            if pos == "売り":
                self.profit_loss = (float(self.unit_price) - float(self.current_price)) * float(self.shares)
            else:
                self.profit_loss = self.market_value - float(self.total_cost)
        except Exception:
            self.profit_loss = 0.0

        # NaN ガード
        if not (self.market_value == self.market_value):
            self.market_value = 0.0
        if not (self.profit_loss == self.profit_loss):
            self.profit_loss = 0.0

        # 正規化したポジションで保存
        self.position = pos

        super().save(*args, **kwargs)

    # ---------- 表示用のラベル（テンプレで使いたい場合は注釈名と衝突しない名前に） ----------
    @property
    def broker_label(self) -> str:
        return dict(self.BROKER_CHOICES).get(self.broker, self.broker)

    @property
    def account_type_label(self) -> str:
        return dict(self.ACCOUNT_TYPE_CHOICES).get(self.account_type, self.account_type)

    @property
    def position_label(self) -> str:
        return dict(self.POSITION_CHOICES).get(self.position, self.position)

    def __str__(self):
        return f"{self.ticker} {self.name}"# 実現損益
# =============================
class RealizedProfit(models.Model):
    TRADE_TYPES = (
        ('sell', '売却'),
        ('dividend', '配当'),
    )

    user          = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='realized_profits')
    date = models.DateField(db_index=True, verbose_name='日付', default="2025-01-01")
    stock_name    = models.CharField(max_length=64, verbose_name='銘柄')
    code          = models.CharField(max_length=16, blank=True, verbose_name='証券コード')
    broker        = models.CharField(max_length=32, blank=True, verbose_name='証券会社')
    account_type  = models.CharField(max_length=32, blank=True, verbose_name='口座区分')
    trade_type    = models.CharField(max_length=16, choices=TRADE_TYPES, default='sell', verbose_name='区分')

    quantity      = models.IntegerField(verbose_name='株数')
    purchase_price= models.IntegerField(null=True, blank=True, verbose_name='取得単価')
    sell_price    = models.IntegerField(null=True, blank=True, verbose_name='売却単価')
    fee           = models.IntegerField(null=True, blank=True, verbose_name='手数料', help_text='マイナスでもOK')

    profit_amount = models.IntegerField(verbose_name='損益額', default=0)
    profit_rate   = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True, verbose_name='損益率', default=0)
    
    class Meta:
        ordering = ['-date', '-id']

    def __str__(self):
        return f'{self.date} {self.stock_name} {self.trade_type}'
        
# =============================
# 現金モデル
# =============================
class Cash(models.Model):
    amount = models.IntegerField("金額")
    updated_at = models.DateTimeField("更新日時", auto_now=True)

    def __str__(self):
        return f"Cash: {self.amount}"


# =============================
# 設定画面パスワード
# =============================
class SettingsPassword(models.Model):
    password = models.CharField("設定画面パスワード", max_length=100)

    def __str__(self):
        return "設定画面パスワード"


# =============================
# 下タブとサブメニュー
# =============================
from django.db import models


class BottomTab(models.Model):
    LINK_TYPE_CHOICES = [
        ("url", "URL 直指定"),
        ("view", "Django view 名"),
    ]

    name = models.CharField(max_length=100, verbose_name="タブ名")
    icon = models.CharField(max_length=100, verbose_name="アイコン（CSSクラスなど）")
    url_name = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        verbose_name="URL 名またはビュー名",
    )
    link_type = models.CharField(
        max_length=10,
        choices=LINK_TYPE_CHOICES,
        default="view",
        verbose_name="リンクの種類",
    )
    order = models.PositiveIntegerField(default=0, verbose_name="表示順")

    def __str__(self):
        return self.name


class SubMenu(models.Model):
    tab = models.ForeignKey(BottomTab, on_delete=models.CASCADE, related_name="submenus")
    name = models.CharField(max_length=100, verbose_name="サブメニュー名")
    url = models.CharField(max_length=200, verbose_name="URL")
    order = models.PositiveIntegerField(default=0, verbose_name="表示順")

    def __str__(self):
        return f"{self.tab.name} - {self.name}"