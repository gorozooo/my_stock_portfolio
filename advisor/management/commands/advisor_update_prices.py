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
        max_age = options["max_age_min"]

        # --- ユーザー探索強化 ---
        user = None
        for model in [WatchEntry, Holding, TrendResult]:
            try:
                obj = model.objects.first()
                if obj and hasattr(obj, "user") and obj.user:
                    user = obj.user
                    break
            except Exception:
                pass
        if not user:
            self.stdout.write(self.style.WARNING("⚠ No valid user found in any table"))
            return

        tickers = self._get_targets(user)
        self.stdout.write(self.style.NOTICE(f"[advisor_update_prices] Target tickers: {len(tickers)}"))

        for t in tickers:
            t_clean = t.strip().upper()
            last_price = None
            tried_symbols = []

            # --- 複数サフィックスを試行 ---
            for suffix in [".T", ".JP", ".TYO", ""]:
                symbol = f"{t_clean}{suffix}"
                tried_symbols.append(symbol)
                try:
                    df = yf.download(symbol, period="5d", interval="1d", progress=False, auto_adjust=False)
                    if not df.empty and "Close" in df.columns:
                        last_price = float(df["Close"].iloc[-1].item())
                        PriceCache.objects.update_or_create(
                            ticker=t_clean,
                            defaults={"last_price": last_price, "updated_at": timezone.now()},
                        )
                        self.stdout.write(self.style.SUCCESS(f"✓ {t_clean} ({symbol}) {last_price:.2f}"))
                        break
                except Exception as e:
                    continue

            if last_price is None:
                PriceCache.objects.update_or_create(
                    ticker=t_clean,
                    defaults={"last_price": 0.0, "updated_at": timezone.now()},
                )
                self.stdout.write(self.style.WARNING(f"⚠ {t_clean} failed ({', '.join(tried_symbols)})"))

            time.sleep(1)  # Yahooレート制限回避

        self.stdout.write(self.style.SUCCESS("✅ PriceCache update completed."))

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