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
    ticker = models.CharField("証券コード", max_length=4)
    name = models.CharField("銘柄名", max_length=100)
    account_type = models.CharField("口座区分", max_length=10, default="現物")
    sector = models.CharField("セクター", max_length=50, default="")
    shares = models.PositiveIntegerField("株数")
    unit_price = models.FloatField("取得単価")
    total_cost = models.PositiveIntegerField("取得額")
    broker = models.CharField("証券会社", max_length=20, choices=BROKER_CHOICES, default="楽天") 
    created_at = models.DateTimeField("作成日時", default=timezone.now)
    updated_at = models.DateTimeField("更新日時", auto_now=True)

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
LINK_TYPE_CHOICES = (
    ('view', '内部ビュー名'),
    ('url', '外部リンク'),
    ('dummy', 'ダミーリンク'),
)


class BottomTab(models.Model):
    name = models.CharField("タブ名", max_length=50)
    icon = models.CharField("アイコン", max_length=50, blank=True)
    url_name = models.CharField("内部ビュー名 or URL", max_length=200, blank=True)
    link_type = models.CharField("リンクタイプ", max_length=10, choices=LINK_TYPE_CHOICES, default='view')
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
