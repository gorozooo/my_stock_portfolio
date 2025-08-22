from django.db import models

class Stock(models.Model):
    name = models.CharField(max_length=100)
    updated_at = models.DateTimeField(auto_now=True)

class RealizedTrade(models.Model):
    name = models.CharField(max_length=100)
    updated_at = models.DateTimeField(auto_now=True)

class Cash(models.Model):
    amount = models.IntegerField()
    updated_at = models.DateTimeField(auto_now=True)

class BottomTab(models.Model):
    name = models.CharField(max_length=50)          # 表示名
    icon_class = models.CharField(max_length=50)   # FontAwesomeのクラス
    url_name = models.CharField(max_length=50)     # DjangoのURL名
    order = models.IntegerField(default=0)         # 並び順
    is_active = models.BooleanField(default=True)  # 表示するか
    is_submenu = models.BooleanField(default=False)# サブメニューかどうか

    class Meta:
        ordering = ['order']

    def __str__(self):
        return self.name