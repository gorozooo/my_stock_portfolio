from __future__ import annotations
import argparse
from django.core.management.base import BaseCommand
from advisor.services.evaluator import evaluate_watchlist

class Command(BaseCommand):
    help = "ウォッチ×ポリシーを評価して、LINEへ通知（重複抑止あり）"

    def add_arguments(self, parser: argparse.ArgumentParser):
        parser.add_argument("--window", type=str, default="daily",
                            help="preopen / intraday / afterclose / daily")

    def handle(self, *args, **opts):
        window = (opts.get("window") or "daily").lower()
        sent, skipped = evaluate_watchlist(window=window)
        self.stdout.write(self.style.SUCCESS(f"evaluate_triggers done: sent={sent}, skipped={skipped}, window={window}"))