# -*- coding: utf-8 -*-
from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.utils.timezone import now

from aiapp.services.fetch_master import refresh_master, DEFAULT_JPX_XLS_URL

class Command(BaseCommand):
    help = "週1：JPXマスタ更新（添付Excel直読込 → DB upsert）。市場区分は扱わない。"

    def add_arguments(self, parser):
        parser.add_argument(
            "--url",
            type=str,
            default=None,
            help=f"JPX Excel URL（省略時は既定: {DEFAULT_JPX_XLS_URL}）",
        )

    def handle(self, *args, **kwargs):
        source = kwargs.get("url")
        try:
            stats = refresh_master(source_url=source)
        except Exception as e:
            raise CommandError(f"weekly_master failed: {e}")

        ts = now().strftime("%Y-%m-%d %H:%M:%S")
        self.stdout.write(
            self.style.SUCCESS(
                f"[{ts}] weekly_master ok: upserted(new)={stats.upserted} / touched={stats.touched_codes}"
            )
        )