# aiapp/management/commands/build_home_snapshot.py
from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.utils import timezone

from aiapp.services.home_snapshot import upsert_today_snapshot


class Command(BaseCommand):
    help = "Build HomeDeckSnapshot for active users (daily)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--user",
            type=int,
            default=0,
            help="user id (optional). If provided, build only for that user id.",
        )
        parser.add_argument(
            "--username",
            type=str,
            default="",
            help="username (optional). If provided, build only for that username.",
        )

    def handle(self, *args, **opts):
        User = get_user_model()
        user_id = int(opts.get("user") or 0)
        username = (opts.get("username") or "").strip()

        qs = User.objects.filter(is_active=True)

        if user_id > 0:
            qs = qs.filter(id=user_id)
        elif username:
            qs = qs.filter(username=username)

        n = 0
        for u in qs.iterator():
            upsert_today_snapshot(u)
            n += 1

        self.stdout.write(self.style.SUCCESS(
            f"[OK] build_home_snapshot: {n} user(s) @ {timezone.now().isoformat()}"
        ))