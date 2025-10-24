from django.db import models

class ActionLog(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    user = models.ForeignKey("auth.User", on_delete=models.CASCADE, null=True, blank=True)
    ticker = models.CharField(max_length=32)
    policy_id = models.CharField(max_length=64, blank=True)  # 使わなければ空
    action = models.CharField(max_length=32)  # save_order / remind / reject
    note = models.TextField(blank=True, default="")

    def __str__(self):
        return f"{self.created_at:%Y-%m-%d %H:%M} {self.ticker} {self.action}"

class Reminder(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    fire_at = models.DateTimeField()  # 送信予定時刻（JST）
    user = models.ForeignKey("auth.User", on_delete=models.CASCADE, null=True, blank=True)
    ticker = models.CharField(max_length=32)
    message = models.CharField(max_length=255)
    done = models.BooleanField(default=False)

    def __str__(self):
        return f"{self.ticker} @ {self.fire_at:%Y-%m-%d %H:%M} done={self.done}"