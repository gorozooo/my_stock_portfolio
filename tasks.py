from __future__ import annotations
from celery import shared_task
from django.contrib.auth import get_user_model
from .services.snapshot import save_daily_snapshot

User = get_user_model()

@shared_task
def task_snapshot_assets_all_users():
    for u in User.objects.all():
        save_daily_snapshot(u)
