from __future__ import annotations
from django.core.management.base import BaseCommand
from django.utils.timezone import now
from aiapp.services.fetch_master import refresh_master

class Command(BaseCommand):
    help = "週1：JPXマスタ更新（CSV→DB反映）"

    def add_arguments(self, parser):
        parser.add_argument("--url", type=str, default=None, help="JPX CSV URL or local path")

    def handle(self, *args, **kwargs):
        source = kwargs.get("url")
        n = refresh_master(source_url=source)
        ts = now().strftime("%Y-%m-%d %H:%M:%S")
        self.stdout.write(self.style.SUCCESS(f"[{ts}] weekly_master upserted {n} rows"))
