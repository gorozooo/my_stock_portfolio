# advisor/management/commands/update_prices.py
from __future__ import annotations
from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
from typing import List
import math

from advisor.models_cache import PriceCache

TICKERS: List[str] = ["8035.T","7203.T","6758.T","8267.T","8306.T"]  # 後でDBに

def _fetch_prices(tickers: List[str]) -> dict[str, int]:
    """
    yfinance等で取得。ライブラリ不在/API障害でも壊れないようフェイルソフト。
    実装例：
        import yfinance as yf
        data = yf.download(" ".join(tickers), period="1d", interval="1d", group_by='ticker', progress=False)
    ここではダミー(±5%)のランダム歩行にしておく。
    """
    import random
    out = {}
    for t in tickers:
        base = {
            "8035.T": 12450, "7203.T": 3150, "6758.T": 14680, "8267.T": 3180, "8306.T": 1470
        }.get(t.upper(), 3000)
        drift = 1.0 + random.uniform(-0.05, 0.05)
        out[t] = int(round(base * drift))
    return out

class Command(BaseCommand):
    help = "価格キャッシュを差分更新（15-30分間隔想定）"

    def add_arguments(self, parser):
        parser.add_argument("--max-age-min", type=int, default=30, help="これより古いものだけ更新")

    def handle(self, *args, **opts):
        max_age = int(opts["max_age_min"])
        since = timezone.now() - timedelta(minutes=max_age)

        # 差分対象
        targets = []
        for t in TICKERS:
            pc = PriceCache.objects.filter(ticker=t).first()
            if (not pc) or (pc.updated_at < since):
                targets.append(t)

        if not targets:
            self.stdout.write(self.style.SUCCESS("PriceCache up-to-date"))
            return

        prices = _fetch_prices(targets)
        for t in targets:
            p = int(prices.get(t, 0)) or None
            if p:
                PriceCache.objects.update_or_create(
                    ticker=t, defaults={"last_price": p, "source": "cache-task"}
                )
        self.stdout.write(self.style.SUCCESS(f"Updated: {', '.join(targets)}"))
