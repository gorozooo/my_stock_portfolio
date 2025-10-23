# portfolio/management/commands/scan_positions.py
from django.core.management.base import BaseCommand
from ...services.trader import evaluate_positions

class Command(BaseCommand):
    help = "保有中ポジションをスキャンしてSTOP/TP/クローズを判定し通知"

    def handle(self, *args, **options):
        self.stdout.write("Scanning open positions...")
        evaluate_positions()
        self.stdout.write("Done.")