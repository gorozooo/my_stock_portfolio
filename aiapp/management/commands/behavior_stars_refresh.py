# aiapp/management/commands/behavior_stars_refresh.py
from __future__ import annotations

from django.core.management.base import BaseCommand, CommandParser

from aiapp.services.behavior_stats_service import refresh_all


class Command(BaseCommand):
    help = "本番用⭐️（銘柄×mode_period×mode_aggr）を直近N日で集計してDBへ保存"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--days",
            type=int,
            default=90,
            help="直近何日で集計するか（デフォルト: 90）",
        )

    def handle(self, *args, **options) -> None:
        days = int(options["days"])
        self.stdout.write(f"[behavior_stars_refresh] start days={days}")

        info = refresh_all(window_days=days)

        self.stdout.write(self.style.SUCCESS(
            f"[behavior_stars_refresh] done updated={info.get('updated')} keys={info.get('total_keys')} days={info.get('window_days')}"
        ))