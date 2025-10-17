# portfolio/management/commands/sector_map_fill.py
# -*- coding: utf-8 -*-
from __future__ import annotations
import os, json
from datetime import date
from typing import Dict, Any
from django.core.management.base import BaseCommand
from django.conf import settings

from ...services.market import latest_sector_strength, _market_dir  # _market_dir は前に作ってるやつ
from ...services.sector_map import JPX33

class Command(BaseCommand):
    help = "最新のセクターRSをJPX33で完全に埋め、media/market/sectors_YYYY-MM-DD.json に出力"

    def add_arguments(self, parser):
        parser.add_argument("--overwrite", action="store_true",
                            help="既存の当日ファイルがあっても上書きする")

    def handle(self, *args, **opts):
        mdir = _market_dir()
        os.makedirs(mdir, exist_ok=True)
        today = date.today().isoformat()
        out_path = os.path.join(mdir, f"sectors_{today}.json")

        if os.path.exists(out_path) and not opts["overwrite"]:
            self.stdout.write(self.style.WARNING(f"exists: {out_path} (skip; use --overwrite to force)"))
            return

        src = latest_sector_strength() or {}
        # 既存に合わせて辞書へ
        merged: Dict[str, Dict[str, Any]] = {}
        for sec in JPX33:
            row = dict(src.get(sec) or {})
            row.setdefault("rs_score", 0.0)
            row.setdefault("advdec", None)
            row.setdefault("vol_ratio", None)
            row.setdefault("date", today)
            merged[sec] = row

        payload = {"date": today, "data": [
            {"sector": sec, **vals} for sec, vals in merged.items()
        ]}
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

        self.stdout.write(self.style.SUCCESS(
            f"wrote JPX33-filled sectors → {out_path} ({len(payload['data'])} sectors)"
        ))