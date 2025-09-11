from __future__ import annotations
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from django.utils import timezone

from ...services.snapshot import save_daily_snapshot

User = get_user_model()


class Command(BaseCommand):
    help = "全ユーザー分の総資産スナップショットを当日分として保存します。"

    def add_arguments(self, parser):
        parser.add_argument(
            "--user",
            type=str,
            default=None,
            help="対象ユーザーのusername（未指定なら全ユーザー）",
        )

    def handle(self, *args, **options):
        target_username = options.get("user")
        now = timezone.now()
        self.stdout.write(self.style.MIGRATE_HEADING(
            f"[{now:%Y-%m-%d %H:%M:%S}] AssetSnapshot: daily save start"
        ))

        qs = User.objects.all()
        if target_username:
            qs = qs.filter(username=target_username)

        count = 0
        for u in qs:
            save_daily_snapshot(u)
            count += 1

        self.stdout.write(self.style.SUCCESS(
            f"Done. saved snapshots for {count} user(s)."
        ))
