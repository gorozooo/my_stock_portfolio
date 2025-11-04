from __future__ import annotations
from django.core.management.base import BaseCommand
from datetime import datetime
from ai.tasks.build_snapshot import build_snapshot_for_date

class Command(BaseCommand):
    help = "raw/*.csv を結合して snapshots/YYYY-MM-DD/ohlcv.csv を生成"

    def add_arguments(self, parser):
        parser.add_argument('--date', type=str, default=None, help='スナップショット日 YYYY-MM-DD')

    def handle(self, *args, **opts):
        date_str = opts['date'] or datetime.now().date().isoformat()
        n_codes, n_rows, outp = build_snapshot_for_date(date_str)
        self.stdout.write(self.style.SUCCESS(f"snapshot built: codes={n_codes} rows={n_rows} -> {outp}"))