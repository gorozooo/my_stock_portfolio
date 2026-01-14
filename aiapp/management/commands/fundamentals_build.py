# aiapp/management/commands/fundamentals_build.py
# -*- coding: utf-8 -*-
"""
Fundamentals（日次）を作る管理コマンド（ルートB: EDINET→銘柄コードへ寄せる）。

現状は 2段構え：
1) 既存の「EDINET集計JSON（edinet_agg_YYYYMMDD.json）」がある前提で、
2) edinet_code_map.json を使って ticker(4桁) へ集約したJSONを生成する。

生成物:
- media/aiapp/fundamentals/daily/YYYYMMDD/edinet_by_ticker_YYYYMMDD.json

将来:
- ここに「EDINET API/取得」や「決算指標（売上/利益/ROE等）」も足していく
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Optional

from django.core.management.base import BaseCommand

from aiapp.services.fundamentals.edinet_code_map_service import build_by_ticker_for_day

JST = timezone(timedelta(hours=9))


class Command(BaseCommand):
    help = "Build fundamentals daily JSON (EDINET aggregated -> by ticker) [Route B]."

    def add_arguments(self, parser):
        parser.add_argument(
            "--day",
            default=None,
            help="Target day. e.g. 20260114 or 2026-01-14 (default: today JST).",
        )
        parser.add_argument(
            "--overwrite",
            action="store_true",
            default=False,
            help="Overwrite output even if exists.",
        )

    def handle(self, *args, **options):
        day = options.get("day")
        overwrite = bool(options.get("overwrite"))

        if not day:
            day = datetime.now(JST).strftime("%Y%m%d")

        res: Dict[str, Any] = build_by_ticker_for_day(str(day), overwrite=overwrite)

        if res.get("ok"):
            self.stdout.write(self.style.SUCCESS(f"[fundamentals_build] ok: {res.get('path')}"))
            meta = res.get("meta") or {}
            if isinstance(meta, dict):
                self.stdout.write(
                    f"  ticker_count={meta.get('ticker_count')} unknown_edinet={meta.get('unknown_edinet_count')}"
                )
        else:
            self.stdout.write(self.style.WARNING(f"[fundamentals_build] not ok: {res.get('path')}"))
            err = res.get("error")
            if err:
                self.stdout.write(f"  error={err}")