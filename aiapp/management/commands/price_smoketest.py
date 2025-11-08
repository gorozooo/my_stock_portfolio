# -*- coding: utf-8 -*-
from __future__ import annotations

from django.core.management.base import BaseCommand
from aiapp.services.fetch_price import get_prices

class Command(BaseCommand):
    help = "価格取得のスモークテスト"

    def add_arguments(self, parser):
        parser.add_argument("--codes", nargs="+", default=["7203","6758","7974"])
        parser.add_argument("--nbars", type=int, default=180)

    def handle(self, *args, **opts):
        codes = opts["codes"]
        nbars = opts["nbars"]
        for c in codes:
            df = get_prices(c, nbars)
            start = df.index.min() if not df.empty else None
            end   = df.index.max() if not df.empty else None
            self.stdout.write(f"{c} rows={len(df)} start={start} end={end}")