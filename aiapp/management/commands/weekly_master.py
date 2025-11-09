# aiapp/management/commands/weekly_master.py
from __future__ import annotations

from django.core.management.base import BaseCommand
from aiapp.services.fetch_master import refresh_master


class Command(BaseCommand):
    help = "週1：JPXマスタ更新（CSV/XLS → DB upsert）。insert と update をカウント表示。"

    def add_arguments(self, parser):
        parser.add_argument(
            "--url",
            type=str,
            default=None,
            help="JPX CSV/XLS URL またはローカルパス（未指定なら既定URL）",
        )

    def handle(self, *args, **kwargs):
        source = kwargs.get("url")
        stats = refresh_master(source_url=source)
        msg = (
            f"[{stats['ts']}] weekly_master "
            f"input={stats['total_input']}  "
            f"inserted={stats['inserted']}  "
            f"updated={stats['updated']}  "
            f"rows_after={stats['after_rows']}  "
            f"missing_sector={stats['missing_sector']}"
        )
        self.stdout.write(self.style.SUCCESS(msg))