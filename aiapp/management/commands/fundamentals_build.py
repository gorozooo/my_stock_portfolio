# aiapp/management/commands/fundamentals_build.py
# -*- coding: utf-8 -*-
"""
財務ファンダスナップショット生成コマンド。

- media/aiapp/fundamentals/input_fund.json を読み
- スコア付与して latest_fund.json に保存
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from aiapp.services.fundamentals.build_service import build_fundamentals_from_input


class Command(BaseCommand):
    help = "財務ファンダスナップショット生成（input_fund.json → latest_fund.json）"

    def add_arguments(self, parser):
        parser.add_argument("--quiet", action="store_true")

    def handle(self, *args, **opts):
        quiet = bool(opts.get("quiet"))
        snap = build_fundamentals_from_input()
        if not quiet:
            self.stdout.write(self.style.SUCCESS(f"fundamentals_build: asof={snap.asof} rows={len(snap.rows)}"))