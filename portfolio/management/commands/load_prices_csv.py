# -*- coding: utf-8 -*-
from __future__ import annotations
import csv
from datetime import datetime, timezone
from django.core.management.base import BaseCommand, CommandParser
from django.utils import timezone as dj_tz
from portfolio.models import Holding

class Command(BaseCommand):
    help = "CSV( ticker,last_price[,date] ) で Holding.last_price を更新する"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("csv_path", type=str, help="CSVファイルパス")
        parser.add_argument("--date-col", type=str, default="date",
                            help="日付カラム名（任意。無ければ現在時刻）")
        parser.add_argument("--ticker-col", type=str, default="ticker")
        parser.add_argument("--price-col", type=str, default="last_price")
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **opts):
        path = opts["csv_path"]
        tcol = opts["ticker_col"]; pcol = opts["price_col"]; dcol = opts["date_col"]
        dry  = opts["dry_run"]

        n_all = n_hit = n_upd = 0
        with open(path, newline="", encoding="utf-8") as f:
            r = csv.DictReader(f)
            for row in r:
                n_all += 1
                ticker = (row.get(tcol) or "").strip()
                if not ticker: continue
                try:
                    price = float(row.get(pcol))
                except Exception:
                    continue
                dtxt = (row.get(dcol) or "").strip()
                dt = None
                if dtxt:
                    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
                        try:
                            dt = datetime.strptime(dtxt, fmt).replace(tzinfo=timezone.utc)
                            break
                        except Exception:
                            pass
                if not dt:
                    dt = dj_tz.now()

                # 同一ティッカーの全保有を更新（現物/信用など複数行に対応）
                qs = Holding.objects.filter(ticker=ticker)
                if not qs.exists():
                    continue
                n_hit += qs.count()
                if not dry:
                    qs.update(last_price=price, last_price_updated=dt)
                    n_upd += qs.count()

        self.stdout.write(self.style.SUCCESS(
            f"[load_prices_csv] rows={n_all} matched={n_hit} updated={n_upd} dry={dry}"
        ))