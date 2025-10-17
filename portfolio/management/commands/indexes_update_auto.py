# -*- coding: utf-8 -*-
from django.core.management.base import BaseCommand
from portfolio.services.indexes_auto import fetch_index_rs

class Command(BaseCommand):
    help = "主要指数群のRSスナップショットを自動生成して保存（indexes_YYYY-MM-DD.json）"

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=20, help="リターン計算期間 default=20")

    def handle(self, *args, **opts):
        days = int(opts["days"])
        result = fetch_index_rs(days=days)
        self.stdout.write(self.style.SUCCESS(f"✅ {len(result['data'])} indexes updated ({result['date']})"))