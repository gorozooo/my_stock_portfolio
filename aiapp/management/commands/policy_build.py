# aiapp/management/commands/policy_build.py
# -*- coding: utf-8 -*-
"""
policy_build（Hybrid用：ファンダ/政策コンテキストから “セクター方針スコア” をJSON化）

このコマンドは「入口」だけ。
中身の計算は aiapp.services.policy_build.build_service に寄せて、
将来拡張してもコマンドが太らないようにする。
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from aiapp.services.policy_build.build_service import build_policy_snapshot, emit_policy_json


class Command(BaseCommand):
    help = "policy_build: セクター方針スコアJSONを生成（Hybrid用）"

    def handle(self, *args, **opts):
        snap = build_policy_snapshot()
        emit_policy_json(snap)

        self.stdout.write(self.style.SUCCESS(
            f"policy_build: asof={snap.asof} sectors={len(snap.sector_rows)}"
        ))