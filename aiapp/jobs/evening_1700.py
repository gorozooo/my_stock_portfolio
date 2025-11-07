from django.core.management.base import BaseCommand
from django.utils.timezone import now

class Command(BaseCommand):
    help = "17:00 10銘柄生成→通知雛形"

    def handle(self, *args, **kwargs):
        ts = now().strftime("%Y-%m-%d %H:%M:%S")
        self.stdout.write(self.style.SUCCESS(f"[{ts}] 17:00 job (stub) OK"))
