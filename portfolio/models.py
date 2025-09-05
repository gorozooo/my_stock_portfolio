from django.db import models
from django.utils import timezone
from django.core.exceptions import ValidationError
import re

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


class Stock(models.Model):
    """
    保有株モデル（スマホファーストUIに合わせた表示/集計用の補助も内蔵）
    - JPティッカーは 4桁なら .T を自動付与して取得（to_yf_symbol）
    - save() 時に現値/評価額/損益を自動更新（取得失敗時は現値を維持）
    - 「買/買い」入力のブレをモデル側で吸収（normalize）
    """

    # 証券会社
    BROKER_CHOICES = [
        ("楽天", "楽天"),
        ("松井", "松井"),
        ("moomoo", "moomoo"),
        ("SBI", "SBI"),
    ]

    # 口座区分（フォームと一致）
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
    ticker        = models.CharField("証券コード", max_length=20, db_index=True)  # 例: 7203 または 7203.T
    name          = models.CharField("銘柄名", max_length=100)
    account_type  = models.CharField("口座区分", max_length=10, choices=ACCOUNT_TYPE_CHOICES, default="現物", db_index=True)
    sector        = models.CharField("セクター", max_length=50, default="")
    position      = models.CharField("ポジション", max_length=4, choices=POSITION_CHOICES, default="買い", db_index=True)

    shares     = models.PositiveIntegerField("株数")
    unit_price = models.FloatField("取得単価")

    # 取得額は整数円で扱う前提（shares * unit_price を四捨五入）
    total_cost = models.PositiveIntegerField("取得額", editable=False)

    # 現在値/評価額/損益はビューでの一覧・売却画面で利用（自動更新）
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

        # よく絞り込むキーに複合インデックス（任意）
        indexes = [
            models.Index(fields=["broker", "account_type", "name"]),
            models.Index(fields=["ticker"]),
        ]

    # ---------- 正規化ヘルパ ----------
    @staticmethod
    def to_yf_symbol(ticker: str) -> str:
        """
        yfinance 用のティッカーに正規化。
        - 4桁数字のみ → 東証銘柄として '.T' を付与（例: '7203' → '7203.T'）
        - 既に拡張子あり（'7203.T', 'AAPL' など）→ そのまま
        """
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
        """
        '買' と '買い' の表記揺れを '買い' に統一（売りはそのまま）。
        """
        v = (value or "").strip()
        if v in ("買", "買い"):
            return "買い"
        if v in ("売", "売り"):
            return "売り"
        return v

    # ---------- バリデーション ----------
    def clean(self):
        super().clean()

        # 未来日を禁止
        if self.purchase_date and self.purchase_date > timezone.localdate():
            raise ValidationError({"purchase_date": "購入日に未来日は指定できません。"})

        # ポジション正規化（フォーム側が '買' を送ってきてもOK）
        pos = self.normalize_position(self.position)
        if pos not in dict(self.POSITION_CHOICES):
            raise ValidationError({"position": "ポジションは『買い』または『売り』を選択してください。"})
        self.position = pos

        # 口座区分チェック（保険）
        if self.account_type not in dict(self.ACCOUNT_TYPE_CHOICES):
            raise ValidationError({"account_type": "口座区分の値が不正です。"})

    # ---------- 保存時の自動計算 ----------
    def save(self, *args, **kwargs):
        """
        - total_cost を shares * unit_price から整数円で自動計算
        - 可能なら yfinance で現在値を軽く更新（失敗時は現値を維持）
        - 評価額/損益をポジション別に計算
        """
        # 取得額（整数円）を自動計算
        try:
            self.total_cost = int(round(float(self.shares) * float(self.unit_price)))
        except Exception:
            self.total_cost = 0

        # 現在値の更新（ネットワーク失敗時は既存の current_price を保持）
        try:
            symbol = self.to_yf_symbol(self.ticker)
            if symbol:
                price_series = yf.Ticker(symbol).history(period="1d")["Close"]
                if len(price_series) > 0:
                    self.current_price = float(price_series.iloc[-1])
        except Exception:
            # 取得できない場合は既存値のまま
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

        # NaN/inf ガード
        if not (self.market_value == self.market_value):  # NaN
            self.market_value = 0.0
        if not (self.profit_loss == self.profit_loss):    # NaN
            self.profit_loss = 0.0

        # 正規化されたポジションで保存
        self.position = pos

        super().save(*args, **kwargs)

    # ---------- 表示系の小さなヘルパ ----------
    @property
    def broker_name(self) -> str:
        """choices のラベルを返す（テンプレの regroup と整合）。"""
        return dict(self.BROKER_CHOICES).get(self.broker, self.broker)

    @property
    def account_type_name(self) -> str:
        """choices のラベルを返す（テンプレの regroup と整合）。"""
        return dict(self.ACCOUNT_TYPE_CHOICES).get(self.account_type, self.account_type)

    @property
    def position_name(self) -> str:
        """choices のラベルを返す。"""
        return dict(self.POSITION_CHOICES).get(self.position, self.position)

    def __str__(self):
        return f"{self.ticker} {self.name}"# =============================
# 実現損益
# =============================
class RealizedTrade(models.Model):
    name = models.CharField("銘柄名", max_length=100)
    updated_at = models.DateTimeField("更新日時", auto_now=True)

    def __str__(self):
        return self.name

class RealizedProfit(models.Model):
    stock_name = models.CharField("銘柄名", max_length=100)
    ticker = models.CharField("証券コード", max_length=10)
    shares = models.PositiveIntegerField("株数")
    purchase_price = models.FloatField("取得単価")
    sell_price = models.FloatField("売却単価")
    total_profit = models.FloatField("損益額")
    sold_at = models.DateTimeField("売却日", default=timezone.now)

    def __str__(self):
        return f"{self.ticker} {self.stock_name} ({self.total_profit})"

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