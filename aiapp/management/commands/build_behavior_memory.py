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

    ※ PRO一択になっても、入力は side なのでそのまま動く。
      （broker は "pro" だけが入る想定）
    """

    help = "AI 行動データから行動メモリ（クセの地図）を構築して保存する（PRO一択）"

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

        # JSON 保存 & メモリ内容取得
        latest_path: Path = svc_memory.save_behavior_memory(user_id=user_id)
        mem = svc_memory.build_behavior_memory(user_id=user_id)

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("===== 行動メモリ サマリ ====="))
        self.stdout.write(f"  user_id        : {mem.get('user_id')}")
        self.stdout.write(f"  total_trades   : {mem.get('total_trades')}")
        self.stdout.write(f"  updated_at     : {mem.get('updated_at')}")
        self.stdout.write("")

        # broker 別の簡易サマリ
        self.stdout.write("  broker:")
        broker_map = mem.get("broker") or {}
        for broker, s in broker_map.items():
            trials = s.get("trials", 0)
            wins = s.get("wins", 0)
            win_rate = s.get("win_rate")
            if win_rate is None:
                win_rate_str = "-"
            else:
                win_rate_str = f"{win_rate:.1f}%"
            self.stdout.write(
                f"    - {broker}: trials={trials} wins={wins} win_rate={win_rate_str}"
            )

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"  → 保存先: {latest_path}"))
        self.stdout.write(self.style.SUCCESS("[build_behavior_memory] 完了"))