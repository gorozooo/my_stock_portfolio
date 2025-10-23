from django.core.management.base import BaseCommand
from ...services.trader import evaluate_positions

class Command(BaseCommand):
    help = "保有中ポジションを監視してSTOP/TPを自動判定"

    def handle(self, *args, **options):
        self.stdout.write("Scanning open positions...")
        evaluate_positions()
        self.stdout.write("Done.")