# aiapp/management/commands/build_home_snapshot.py
from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.utils import timezone

from aiapp.services.home_snapshot import upsert_today_snapshot


class Command(BaseCommand):
    help = "Build HomeDeckSnapshot for all active users (daily)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--user",
            type=str,
            default="",
            help="username (optional). If provided, build only for that user.",
        )

    def handle(self, *args, **opts):
        User = get_user_model()
        username = (opts.get("user") or "").strip()

        if username:
            qs = User.objects.filter(username=username)
        else:
            qs = User.objects.all()

        n = 0
        for u in qs.iterator():
            upsert_today_snapshot(u)
            n += 1

        self.stdout.write(self.style.SUCCESS(
            f"[OK] build_home_snapshot: {n} user(s) @ {timezone.now().isoformat()}"
        ))