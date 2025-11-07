from django.db import models

class StockMaster(models.Model):
    code = models.CharField(max_length=8, unique=True)  # 例: "6758"
    name = models.CharField(max_length=64)             # 日本語名
    sector33 = models.CharField(max_length=64)         # 33業種（日本語）
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "aiapp_stock_master"

    def __str__(self):
        return f"{self.name}({self.code})"
