from django.core.management.base import BaseCommand
from django.utils.timezone import now

class Command(BaseCommand):
    help = "07:00 前日結果集計→保有アラート雛形"

    def handle(self, *args, **kwargs):
        ts = now().strftime("%Y-%m-%d %H:%M:%S")
        self.stdout.write(self.style.SUCCESS(f"[{ts}] 07:00 job (stub) OK"))
