from django.core.management.base import BaseCommand
from django.utils import timezone
import yfinance as yf
from advisor.models_cache import PriceCache
from advisor.models import WatchEntry
from advisor.models_trend import TrendResult
from portfolio.models import Holding

class Command(BaseCommand):
    help = "Update PriceCache for all relevant tickers (WatchEntry + Holding + TrendResult)."

    def add_arguments(self, parser):
        parser.add_argument("--max-age-min", type=int, default=30)

    def handle(self, *args, **options):
        max_age = options["max_age_min"]
        user = WatchEntry.objects.first().user if WatchEntry.objects.exists() else None
        if not user:
            self.stdout.write(self.style.WARNING("No user found"))
            return

        tickers = self._get_targets(user)
        self.stdout.write(f"Updating {len(tickers)} tickers...")

        for t in tickers:
            try:
                df = yf.download(f"{t}.T", period="5d", interval="1d", progress=False)
                if df.empty:
                    continue
                last = float(df["Close"].iloc[-1])
                PriceCache.objects.update_or_create(
                    ticker=t.upper(),
                    defaults={"last_price": last, "updated_at": timezone.now()}
                )
                self.stdout.write(self.style.SUCCESS(f"Updated: {t}"))
            except Exception as e:
                self.stdout.write(self.style.WARNING(f"Skip {t}: {e}"))

        self.stdout.write(self.style.SUCCESS("PriceCache update done."))

    def _get_targets(self, user):
        s = set()
        s |= {w.ticker.upper() for w in WatchEntry.objects.filter(user=user)}
        s |= {h.ticker.upper() for h in Holding.objects.filter(user=user)}
        s |= {t.ticker.upper() for t in TrendResult.objects.filter(user=user).order_by("-asof")[:300]}
        return sorted(s)