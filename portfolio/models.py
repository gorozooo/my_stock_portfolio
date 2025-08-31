from django.db import models
from django.utils import timezone

# =============================
# 株マスター（証券コード・銘柄・33業種）
# =============================
class StockMaster(models.Model):
    code = models.CharField("証券コード", max_length=4, unique=True, db_index=True)
    name = models.CharField("銘柄名", max_length=200)
    sector = models.CharField("33業種", max_length=100, blank=True)

    def __str__(self):
        return f"{self.code} {self.name}"

# =============================
# 保有株モデル
# =============================
class Stock(models.Model):
    BROKER_CHOICES = [
        ("楽天", "楽天"),
        ("松井", "松井"),
        ("moomoo", "moomoo"),
        ("SBI", "SBI"),
    ]

    purchase_date = models.DateField("購入日")
    ticker = models.CharField("証券コード", max_length=10)  # ← 日本株は 7203.T など
    name = models.CharField("銘柄名", max_length=100)
    account_type = models.CharField("口座区分", max_length=10, default="現物")
    sector = models.CharField("セクター", max_length=50, default="")
    shares = models.PositiveIntegerField("株数")
    unit_price = models.FloatField("取得単価")
    total_cost = models.PositiveIntegerField("取得額", editable=False)  # 自動計算
    current_price = models.FloatField("現在株価", default=0)  # 自動取得
    market_value = models.FloatField("評価額", default=0, editable=False)  # 自動計算
    profit_loss = models.FloatField("損益額", default=0, editable=False)  # 自動計算
    broker = models.CharField("証券会社", max_length=20, choices=BROKER_CHOICES, default="楽天")
    note = models.TextField("メモ", blank=True, default="")
    created_at = models.DateTimeField("作成日時", default=timezone.now)
    updated_at = models.DateTimeField("更新日時", auto_now=True)

    def save(self, *args, **kwargs):
        # 取得額を自動計算
        self.total_cost = self.shares * self.unit_price

        # 株価を Yahoo Finance から取得
        try:
            ticker_symbol = self.ticker
            if not ticker_symbol.endswith(".T"):  # 日本株の場合
                ticker_symbol += ".T"
            stock_data = yf.Ticker(ticker_symbol)
            price = stock_data.history(period="1d")["Close"].iloc[-1]
            self.current_price = float(price)
        except Exception:
            pass  # エラー時は前回の値をそのまま残す

        # 評価額と損益を自動計算
        self.market_value = self.shares * self.current_price
        self.profit_loss = self.market_value - self.total_cost

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
    name = models.CharField("タブ名", max_length=50)
    icon = models.CharField("アイコン", max_length=50, blank=True)
    order = models.PositiveIntegerField("並び順", default=0)

    class Meta:
        ordering = ['order']

    def __str__(self):
        return self.name


class SubMenu(models.Model):
    tab = models.ForeignKey(BottomTab, on_delete=models.CASCADE, related_name='submenus')
    name = models.CharField("サブメニュー名", max_length=50)
    url = models.CharField("URL", max_length=200)
    order = models.PositiveIntegerField("並び順", default=0)

    class Meta:
        ordering = ['order']

    def __str__(self):
        return f"{self.tab.name} -> {self.name}"