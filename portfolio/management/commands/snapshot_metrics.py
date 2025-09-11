from django.core.management.base import BaseCommand
from django.utils import timezone
from django.contrib.auth import get_user_model
from portfolio.models import PortfolioSnapshot
from portfolio.views_main import compute_portfolio_totals  # ←下で出す関数

class Command(BaseCommand):
    help = "Save daily total_assets snapshot for each user (16:00 JST cron)."

    def handle(self, *args, **opts):
        User = get_user_model()
        today = timezone.localdate()
        for u in User.objects.all():
            totals = compute_portfolio_totals(user=u)  # total_assets を返す簡易関数
            ta = int(round(totals["total_assets"]))
            obj, created = PortfolioSnapshot.objects.update_or_create(
                user=u, date=today, defaults={"total_assets": ta}
            )
            self.stdout.write(self.style.SUCCESS(
                f"[snapshot] {u} {today} total_assets={ta:,} ({'created' if created else 'updated'})"
            ))