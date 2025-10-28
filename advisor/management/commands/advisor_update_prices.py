import time
import yfinance as yf
from django.core.management.base import BaseCommand
from django.utils import timezone
from advisor.models_cache import PriceCache
from advisor.models import WatchEntry
from advisor.models_trend import TrendResult
from portfolio.models import Holding


class Command(BaseCommand):
    help = "Update PriceCache for all relevant tickers (WatchEntry + Holding + TrendResult)."

    def add_arguments(self, parser):
        parser.add_argument("--max-age-min", type=int, default=30)

    def handle(self, *args, **options):
        max_age = options["max-age-min"]
        user = WatchEntry.objects.first().user if WatchEntry.objects.exists() else None
        if not user:
            self.stdout.write(self.style.WARNING("No user found"))
            return

        tickers = self._get_targets(user)
        self.stdout.write(self.style.NOTICE(f"[advisor_update_prices] Updating {len(tickers)} tickers..."))

        for t in tickers:
            t_clean = t.strip().upper()
            last_price = None
            tried = []

            for suffix in [".T", ".JP", ""]:
                symbol = f"{t_clean}{suffix}"
                tried.append(symbol)
                try:
                    df = yf.download(symbol, period="5d", interval="1d", progress=False)
                    if not df.empty:
                        last_price = float(df["Close"].iloc[-1])
                        PriceCache.objects.update_or_create(
                            ticker=t_clean,
                            defaults={"last_price": last_price, "updated_at": timezone.now()},
                        )
                        self.stdout.write(self.style.SUCCESS(f"✓ {t_clean} ({symbol}) → {last_price:.2f}"))
                        break
                except Exception as e:
                    continue

            if not last_price:
                self.stdout.write(self.style.WARNING(f"✗ {t_clean} failed ({', '.join(tried)})"))

            time.sleep(1)  # Yahoo APIレートリミット回避

        self.stdout.write(self.style.SUCCESS("PriceCache update done."))

    def _get_targets(self, user):
        s = set()
        try:
            s |= {w.ticker.upper() for w in WatchEntry.objects.filter(user=user)}
        except Exception:
            pass
        try:
            s |= {h.ticker.upper() for h in Holding.objects.filter(user=user)}
        except Exception:
            pass
        try:
            s |= {t.ticker.upper() for t in TrendResult.objects.filter(user=user).order_by("-asof")[:300]}
        except Exception:
            pass
        return sorted(s)