from __future__ import annotations
from django.core.management.base import BaseCommand
from datetime import datetime
from ai.tasks.fetch_ohlcv import fetch_all

class Command(BaseCommand):
    help = "Yahoo Financeから日本株OHLCV（直近3ヶ月）を取得し、media/ohlcv/raw/<code>.csv を更新"

    def add_arguments(self, parser):
        parser.add_argument('--date', type=str, default=None, help='基準日 YYYY-MM-DD（ログ/ファイル名用）')
        parser.add_argument('--workers', type=int, default=5, help='並列数（デフォルト5）')

    def handle(self, *args, **opts):
        date_str = opts['date'] or datetime.now().date().isoformat()
        workers = int(opts['workers'] or 5)

        res = fetch_all(as_of=date_str, workers=workers)
        ok = len(res['ok'])
        ng = len(res['ng'])
        self.stdout.write(self.style.SUCCESS(f"fetch ok={ok} ng={ng} date={date_str}"))
        if ng:
            self.stdout.write(self.style.WARNING(f"失敗銘柄は media/ohlcv/failures/{date_str}.txt を確認"))