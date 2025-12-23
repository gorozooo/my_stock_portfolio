# aiapp/management/commands/sync_simulate_pro_eval.py
# -*- coding: utf-8 -*-
from __future__ import annotations

from django.core.management.base import BaseCommand, CommandParser

from aiapp.services.simulate_pro_eval_sync import sync_simulate_pro_eval


class Command(BaseCommand):
    help = (
        "VirtualTrade(PRO公式)で確定した評価(replay.pro.last_eval)を "
        "media/aiapp/simulate/*.jsonl へ eval_*_pro として書き戻す（王道A-2）"
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--days", type=int, default=10, help="trade_date基準で何日前まで見るか")
        parser.add_argument("--limit", type=int, default=0, help="0なら全件。>0なら最大件数")
        parser.add_argument("--user", type=int, default=None, help="対象ユーザーID（省略時は全員）")
        parser.add_argument(
            "--date-max",
            type=str,
            default=None,
            help="date_max を固定（YYYY-MM-DD）。省略時は今日",
        )
        parser.add_argument("--dry-run", action="store_true", help="書き込みせず集計だけ")
        # Django標準の -v/--verbosity を使う（options['verbosity']）

    def handle(self, *args, **options) -> None:
        verbose = int(options.get("verbosity", 1) or 1)

        days = int(options.get("days", 10))
        limit = int(options.get("limit", 0))
        user_id = options.get("user")
        date_max = options.get("date_max")
        dry_run = bool(options.get("dry_run"))

        self.stdout.write(
            f"[sync_simulate_pro_eval] start days={days} limit={limit} user={user_id} date_max={date_max} dry_run={dry_run}"
        )

        stats = sync_simulate_pro_eval(
            days=days,
            limit=limit,
            user_id=user_id,
            date_max=date_max,
            dry_run=dry_run,
            verbose=verbose,
        )

        self.stdout.write("")
        self.stdout.write("===== sync_simulate_pro_eval summary =====")
        self.stdout.write(f"  scanned_files       : {stats.scanned_files}")
        self.stdout.write(f"  scanned_lines       : {stats.scanned_lines}")
        self.stdout.write(f"  parsed_records      : {stats.parsed_records}")
        self.stdout.write(f"  target_vtrades      : {stats.target_vtrades}")
        self.stdout.write(f"  matched             : {stats.matched}")
        self.stdout.write(f"  updated_records     : {stats.updated_records}")
        self.stdout.write(f"  updated_files       : {stats.updated_files}")
        self.stdout.write(f"  skipped_no_last_eval: {stats.skipped_no_last_eval}")
        self.stdout.write(f"  skipped_no_qty_pro  : {stats.skipped_no_qty_pro}")
        self.stdout.write(f"  skipped_no_match    : {stats.skipped_no_match}")
        self.stdout.write(self.style.SUCCESS("[sync_simulate_pro_eval] done"))