# aiapp/management/commands/build_behavior_memory.py
from __future__ import annotations

from pathlib import Path
from typing import Optional

from django.conf import settings
from django.core.management.base import BaseCommand, CommandParser

from aiapp.services import behavior_memory as svc_memory


class Command(BaseCommand):
    """
    latest_behavior_side.jsonl から
    「クセの地図（行動メモリ）」を構築して JSON に保存する。

    出力:
      MEDIA_ROOT/aiapp/behavior/memory/
        - YYYYMMDD_behavior_memory_u<user>.json
        - latest_behavior_memory_u<user>.json
    """

    help = "AI 行動データから行動メモリ（クセの地図）を構築して保存する"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--user",
            type=int,
            default=None,
            help="対象ユーザーID（省略時は all として集計）",
        )

    def handle(self, *args, **options) -> None:
        user_id: Optional[int] = options.get("user")

        self.stdout.write(
            f"[build_behavior_memory] MEDIA_ROOT={settings.MEDIA_ROOT} user={user_id}"
        )

        latest_path: Path = svc_memory.save_behavior_memory(user_id=user_id)

        mem = svc_memory.build_behavior_memory(user_id=user_id)

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("===== 行動メモリ サマリ ====="))
        self.stdout.write(f"  user_id        : {mem.get('user_id')}")
        self.stdout.write(f"  total_trades   : {mem.get('total_trades')}")
        self.stdout.write(f"  updated_at     : {mem.get('updated_at')}")
        self.stdout.write("")
        self.stdout.write("  broker:")
        for broker, s in (mem.get("broker") or {}).items():
            self.stdout.write(
                f"    - {broker}: trials={s['trials']} wins={s['wins']} "
                f"win_rate={s['win_rate']:.1f if s['win_rate'] is not None else 0.0}%"
            )
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"  → 保存先: {latest_path}"))
        self.stdout.write(self.style.SUCCESS("[build_behavior_memory] 完了"))