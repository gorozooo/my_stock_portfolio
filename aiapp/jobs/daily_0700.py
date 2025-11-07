from __future__ import annotations
from django.core.management.base import BaseCommand
from django.utils.timezone import now

class Command(BaseCommand):
    help = "07:00 前日結果集計→（現段階はログのみ）"

    def handle(self, *args, **kwargs):
        ts = now().strftime("%Y-%m-%d %H:%M:%S")
        # 将来：VirtualTrade の未クローズを評価、★/RCP更新、保有アラート作成
        self.stdout.write(self.style.SUCCESS(f"[{ts}] 07:00 daily summary (stub)"))
