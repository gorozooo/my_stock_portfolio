from __future__ import annotations
from django.core.management.base import BaseCommand
from django.utils.timezone import now
from django.conf import settings
from aiapp.models import StockMaster
from aiapp.services.fetch_price import get_prices
from aiapp.models.features import compute_features
from aiapp.models.scoring import score_short_aggr

UNIVERSE_LIMIT = int(getattr(settings, "AIAPP_UNIVERSE_LIMIT", 120))

class Command(BaseCommand):
    help = "17:00 10銘柄生成（ログ出力版・通知は未接続）"

    def handle(self, *args, **kwargs):
        ts = now().strftime("%Y-%m-%d %H:%M:%S")
        self.stdout.write(f"[{ts}] start 17:00 picks")
        qs = StockMaster.objects.all().order_by("code")[:UNIVERSE_LIMIT]

        items = []
        for row in qs:
            df = get_prices(row.code, 180)
            if df is None or df.empty:
                continue
            feat = compute_features(df)
            if not feat.get("ok"):
                continue
            score = score_short_aggr(feat, regime=None)
            items.append((row.code, row.name, score))

        items = sorted(items, key=lambda x: x[2], reverse=True)[:10]
        for code, name, sc in items:
            self.stdout.write(f"  {code} {name} score={sc}")
        self.stdout.write(self.style.SUCCESS(f"[{ts}] done 17:00 picks"))
