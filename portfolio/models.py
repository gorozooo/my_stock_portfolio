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

class Stock(models.Model):
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
        ("NISA", "NISA"),
    ]

    # ポジション（追加）
    POSITION_CHOICES = [
        ("買い", "買い"),
        ("売り", "売り"),
    ]

    purchase_date = models.DateField("購入日")
    ticker = models.CharField("証券コード", max_length=20, db_index=True)  # 例: 7203 または 7203.T
    name = models.CharField("銘柄名", max_length=100)
    account_type = models.CharField("口座区分", max_length=10, choices=ACCOUNT_TYPE_CHOICES, default="現物")
    sector = models.CharField("セクター", max_length=50, default="")
    position = models.CharField("ポジション", max_length=4, choices=POSITION_CHOICES, default="買い")  # ← 追加
    shares = models.PositiveIntegerField("株数")
    unit_price = models.FloatField("取得単価")

    # 取得額は整数円で扱いたい要件が多いため PositiveInteger のまま維持
    # （小数の可能性がある場合は FloatField へ変更してください）
    total_cost = models.PositiveIntegerField("取得額", editable=False)  # 自動計算

    current_price = models.FloatField("現在株価", default=0, editable=False)  # 自動取得
    market_value = models.FloatField("評価額", default=0, editable=False)   # 自動計算
    profit_loss = models.FloatField("損益額", default=0, editable=False)     # 自動計算

    broker = models.CharField("証券会社", max_length=20, choices=BROKER_CHOICES, default="楽天")
    note = models.TextField("メモ", blank=True, default="")
    created_at = models.DateTimeField("作成日時", default=timezone.now)
    updated_at = models.DateTimeField("更新日時", auto_now=True)

    class Meta:
        ordering = ["-purchase_date", "-created_at"]
        verbose_name = "保有株"
        verbose_name_plural = "保有株"

    # --- バリデーション（モデルレベル） ---
    def clean(self):
        super().clean()
        # 未来日を禁止
        if self.purchase_date and self.purchase_date > timezone.localdate():
            raise ValidationError({"purchase_date": "購入日に未来日は指定できません。"})
        # ポジション選択チェック（保険）
        if self.position not in dict(self.POSITION_CHOICES):
            raise ValidationError({"position": "ポジションは『買い』または『売り』を選択してください。"})
        # 口座区分チェック（保険）
        if self.account_type not in dict(self.ACCOUNT_TYPE_CHOICES):
            raise ValidationError({"account_type": "口座区分の値が不正です。"})

    # --- ヘルパー：yfinance用ティッカーに正規化 ---
    @staticmethod
    def to_yf_symbol(ticker: str) -> str:
        """
        入力が 4桁数字 or 4桁数字+拡張なし → 日本株として .T を付与。
        すでにドット付き(例: 7203.T / AAPL) はそのまま。
        """
        t = (ticker or "").strip()
        if not t:
            return t
        if "." in t:  # 既に拡張子あり
            return t
        # 4桁の数値のみなら東証銘柄として .T を付与
        if re.fullmatch(r"\d{4}", t):
            return f"{t}.T"
        return t

    def save(self, *args, **kwargs):
        # 取得額（整数円）を自動計算
        # shares * unit_price が小数のとき四捨五入して整数円に
        self.total_cost = int(round(float(self.shares) * float(self.unit_price)))

        # 株価取得（失敗時は前回値を維持）
        try:
            symbol = self.to_yf_symbol(self.ticker)
            if symbol:
                price_series = yf.Ticker(symbol).history(period="1d")["Close"]
                if len(price_series) > 0:
                    self.current_price = float(price_series.iloc[-1])
        except Exception:
            # ネットワーク/銘柄未取得等は無視して現値を保持
            pass

        # 評価額と損益（ポジション別）
        # 買い: 損益 = (現値 * 株数) - 取得額
        # 売り: 損益 = (取得単価 - 現値) * 株数 （空売りの評価損益）
        self.market_value = float(self.shares) * float(self.current_price)
        if self.position == "売り":
            self.profit_loss = (float(self.unit_price) - float(self.current_price)) * float(self.shares)
        else:
            self.profit_loss = self.market_value - float(self.total_cost)

        # 追加の整合チェック（shares や price が NaN/inf にならないよう保険）
        if not (self.market_value == self.market_value):  # NaNチェック
            self.market_value = 0.0
        if not (self.profit_loss == self.profit_loss):
            self.profit_loss = 0.0

        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.ticker} {self.name}"
# =============================
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