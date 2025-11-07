from django.db import models

class VirtualTrade(models.Model):
    code = models.CharField(max_length=8)
    name = models.CharField(max_length=64)
    mode_period = models.CharField(max_length=8)   # "short"/"mid"/"long"
    mode_aggr = models.CharField(max_length=8)     # "aggr"/"norm"/"def"
    entry_px = models.FloatField()
    tp_px = models.FloatField()
    sl_px = models.FloatField()
    qty = models.IntegerField()
    opened_at = models.DateTimeField()
    closed_at = models.DateTimeField(null=True, blank=True)
    result_r = models.FloatField(null=True, blank=True)  # Rで評価
    replay = models.JSONField(default=dict)              # リプレイ用イベント
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "aiapp_virtual_trade"
        indexes = [models.Index(fields=["code", "opened_at"])]

    def __str__(self):
        return f"{self.name}({self.code}) {self.opened_at:%Y-%m-%d}"
