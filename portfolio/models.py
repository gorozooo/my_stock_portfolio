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

class NavigationItem(models.Model):
    name = models.CharField(max_length=50)         # 表示名
    icon = models.CharField(max_length=10, blank=True)  # アイコン（絵文字など）
    url_name = models.CharField(max_length=50)     # Django URL名
    order = models.PositiveIntegerField(default=0) # 並び順
    is_active = models.BooleanField(default=True)  # 表示/非表示

    def __str__(self):
        return self.name

    class Meta:
        ordering = ['order']

class BottomNav(models.Model):
    name = models.CharField(max_length=50)  # 表示名
    icon = models.CharField(max_length=50)  # アイコン（例: 📊, ⚙️ など）
    url_name = models.CharField(max_length=100)  # DjangoのURL name
    order = models.PositiveIntegerField(default=0)  # 表示順
    parent = models.ForeignKey("self", null=True, blank=True,
                               on_delete=models.CASCADE,
                               related_name="children")  # サブメニュー用

    def __str__(self):
        return self.name

    class Meta:
        ordering = ["order"]

from django.urls import reverse

class Menu(models.Model):
    name = models.CharField(max_length=50)
    icon = models.CharField(max_length=10, blank=True)
    url_name = models.CharField(max_length=50)
    parent = models.ForeignKey('self', null=True, blank=True, on_delete=models.CASCADE)
    order = models.PositiveIntegerField(default=0)

    def __str__(self):
        return self.name


class SubMenu(models.Model):
    parent = models.ForeignKey(Menu, on_delete=models.CASCADE, related_name="submenus")
    name = models.CharField(max_length=50)  # サブメニュー名（例: 新規登録）
    url_name = models.CharField(max_length=100, blank=True)

    def __str__(self):
        return f"{self.parent.name} - {self.name}"

    def get_absolute_url(self):
        if self.url_name:
            return reverse(self.url_name)
        return "#"

class Page(models.Model):
    name = models.CharField(max_length=50)
    icon = models.CharField(max_length=10, blank=True)
    url_name = models.CharField(max_length=50)
    parent = models.ForeignKey(
        'self', blank=True, null=True, on_delete=models.CASCADE, related_name='children'
    )
    order = models.PositiveIntegerField(default=0)

    def __str__(self):
        return self.name