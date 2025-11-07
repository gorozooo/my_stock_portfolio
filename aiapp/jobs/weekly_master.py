from django.core.management.base import BaseCommand
from django.utils.timezone import now
from aiapp.services.fetch_master import refresh_master

class Command(BaseCommand):
    help = "週1：JPXマスタ更新（雛形）"

    def handle(self, *args, **kwargs):
        n = refresh_master()
        ts = now().strftime("%Y-%m-%d %H:%M:%S")
        self.stdout.write(self.style.SUCCESS(f"[{ts}] weekly_master updated {n} rows"))
