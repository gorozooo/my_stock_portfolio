from django.db import models

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

class BottomTab(models.Model):
    name = models.CharField(max_length=50)                 # タブの名前
    icon = models.CharField(max_length=50, default='fa-home')  # アイコン（例: fa-home）
    url_name = models.CharField(max_length=50)             # URLの名前（home, settingsなど）
    order = models.PositiveIntegerField(default=0)        # 並び順

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
        return f"{self.tab.name} → {self.name}"

class SettingsPassword(models.Model):
    password = models.CharField(max_length=100, verbose_name="設定画面パスワード")

    def __str__(self):
        return "設定画面パスワード"
