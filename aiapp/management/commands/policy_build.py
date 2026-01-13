# aiapp/management/commands/policy_build.py
# -*- coding: utf-8 -*-
"""
政策・社会情勢スナップショット生成コマンド。

- media/aiapp/policy/input_policy.json を読み
- latest_policy.json に正規化して保存
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from aiapp.services.policy_news.build_service import build_policy_from_input


class Command(BaseCommand):
    help = "政策・社会情勢スナップショット生成（input_policy.json → latest_policy.json）"

    def add_arguments(self, parser):
        parser.add_argument("--quiet", action="store_true")

    def handle(self, *args, **opts):
        quiet = bool(opts.get("quiet"))
        snap = build_policy_from_input()
        if not quiet:
            self.stdout.write(self.style.SUCCESS(f"policy_build: asof={snap.asof} sectors={len(snap.sector_rows)}"))