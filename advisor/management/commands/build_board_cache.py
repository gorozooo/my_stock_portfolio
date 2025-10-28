# advisor/management/commands/build_board_cache.py
from __future__ import annotations
from django.core.management.base import BaseCommand
from django.utils import timezone

from advisor.models_cache import BoardCache
from advisor.services.board_source import build_board

class Command(BaseCommand):
    help = "作戦ボードのキャッシュを作成（朝一 or 1-3時間ごと）"

    def add_arguments(self, parser):
        parser.add_argument("--ttl-min", type=int, default=180)

    def handle(self, *args, **opts):
        payload = build_board(user=None)  # グローバル版
        # build_board が BoardCache を作るのでここはログだけ
        self.stdout.write(self.style.SUCCESS(
            f"Board built (live={payload.get('meta',{}).get('live')}) at {timezone.now()}"
        ))