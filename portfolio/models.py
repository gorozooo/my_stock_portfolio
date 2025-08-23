from django.db import models

# =============================
# 株関連モデル
# =============================
class Stock(models.Model):
    name = models.CharField(max_length=100)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name


class RealizedTrade(models.Model):
    name = models.CharField(max_length=100)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name


class Cash(models.Model):
    amount = models.IntegerField()
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Cash: {self.amount}"


# =============================
# 設定画面パスワード
# =============================
class SettingsPassword(models.Model):
    password = models.CharField(max_length=100, verbose_name="設定画面パスワード")

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
    name = models.CharField(max_length=50)
    icon = models.CharField(max_length=50, blank=True)
    url_name = models.CharField(max_length=200, blank=True)  # 内部ビュー名 or URL
    link_type = models.CharField(max_length=10, choices=LINK_TYPE_CHOICES, default='view')
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['order']

    def __str__(self):
        return self.name


class SubMenu(models.Model):
    tab = models.ForeignKey(BottomTab, on_delete=models.CASCADE, related_name='submenus')
    name = models.CharField(max_length=50)
    url = models.CharField(max_length=200)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['order']

    def __str__(self):
        return f"{self.tab.name} -> {self.name}"
