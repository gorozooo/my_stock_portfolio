# advisor/management/commands/policy_snapshot_daily.py
from __future__ import annotations
from django.core.management.base import BaseCommand
from advisor.services.policy_snapshot import snapshot_all_active_policies

class Command(BaseCommand):
    help = "アクティブなポリシーを日次スナップショット（DB保存＋JSON出力）"

    def add_arguments(self, parser):
        parser.add_argument("--no-files", action="store_true", help="JSONファイル出力を行わない（DBのみ）")

    def handle(self, *args, **opts):
        save_files = not opts.get("no_files", False)
        snaps = snapshot_all_active_policies(save_files=save_files)
        self.stdout.write(self.style.SUCCESS(f"Policy snapshots done: {len(snaps)}"))