from django.db import models
from django.contrib.auth import get_user_model

User = get_user_model()

class ActionLog(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    user = models.ForeignKey(User, on_delete=models.CASCADE, null=True, blank=True)
    ticker = models.CharField(max_length=32)
    policy_id = models.CharField(max_length=64, blank=True)
    action = models.CharField(max_length=32)  # save_order / remind / reject
    note = models.TextField(blank=True, default="")

    class Meta:
        indexes = [
            models.Index(fields=["user", "action", "created_at"]),
            models.Index(fields=["user", "ticker", "created_at"]),
        ]

    def __str__(self):
        return f"{self.created_at:%Y-%m-%d %H:%M} {self.ticker} {self.action}"


class Reminder(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    fire_at = models.DateTimeField()
    user = models.ForeignKey(User, on_delete=models.CASCADE, null=True, blank=True)
    ticker = models.CharField(max_length=32)
    message = models.CharField(max_length=255)
    done = models.BooleanField(default=False)

    class Meta:
        indexes = [
            models.Index(fields=["done", "fire_at"]),
            models.Index(fields=["user", "done", "fire_at"]),
        ]

    def __str__(self):
        return f"{self.ticker} @ {self.fire_at:%Y-%m-%d %H:%M} done={self.done}"


class WatchEntry(models.Model):
    STATUS_ACTIVE = "active"
    STATUS_ARCHIVED = "archived"
    STATUS_CHOICES = [(STATUS_ACTIVE, "active"), (STATUS_ARCHIVED, "archived")]

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    user = models.ForeignKey(User, on_delete=models.CASCADE)
    ticker = models.CharField(max_length=32)
    name = models.CharField(max_length=128, blank=True, default="")
    note = models.CharField(max_length=255, blank=True, default="")
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_ACTIVE)
    in_position = models.BooleanField(default=False)  # IN/OUT トグル

    class Meta:
        unique_together = (("user", "ticker", "status"),)
        indexes = [
            models.Index(fields=["user", "status", "updated_at"]),
            models.Index(fields=["user", "ticker"]),
        ]

    def __str__(self):
        return f"[{self.status}] {self.ticker} ({self.user_id})"