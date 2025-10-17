# -*- coding: utf-8 -*-
from __future__ import annotations
import csv, json
from datetime import datetime
from pathlib import Path
from typing import Dict, Any

from django.core.management.base import BaseCommand, CommandParser
from django.db import transaction

from ...models_market import SectorSignal

def _to_float(x, default=0.0):
    try:
        return float(x)
    except Exception:
        return default

class Command(BaseCommand):
    help = "セクター強弱CSVを取り込みます（列: date,sector,rs_score,advdec,vol_ratio,meta?）"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--csv", required=True, help="入力CSVパス")
        parser.add_argument("--date-col", default="date")
        parser.add_argument("--sector-col", default="sector")
        parser.add_argument("--rs-col", default="rs_score")
        parser.add_argument("--advdec-col", default="advdec")
        parser.add_argument("--vol-col", default="vol_ratio")
        parser.add_argument("--meta-col", default="meta")
        parser.add_argument("--date-format", default="%Y-%m-%d", help="例: 2025-10-15")

    def handle(self, *args, **opts):
        path = Path(opts["csv"])
        if not path.exists():
            self.stderr.write(self.style.ERROR(f"CSV not found: {path}"))
            return

        dcol = opts["date_col"]
        scol = opts["sector_col"]
        rcol = opts["rs_col"]
        acol = opts["advdec_col"]
        vcol = opts["vol_col"]
        mcol = opts["meta_col"]
        dfmt = opts["date_format"]

        created = updated = 0
        with path.open("r", encoding="utf-8") as f, transaction.atomic():
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    dt = datetime.strptime(row[dcol].strip(), dfmt).date()
                    sector = (row[scol] or "").strip() or "未分類"
                except Exception as e:
                    self.stderr.write(self.style.WARNING(f"skip row (date/sector parse): {e} {row}"))
                    continue

                rs = _to_float(row.get(rcol))
                ad = _to_float(row.get(acol))
                vr = _to_float(row.get(vcol), 1.0)

                meta: Dict[str, Any] = {}
                if row.get(mcol):
                    try:
                        meta = json.loads(row[mcol])
                    except Exception:
                        meta = {"raw_meta": row[mcol]}

                obj, is_new = SectorSignal.objects.update_or_create(
                    date=dt, sector=sector,
                    defaults=dict(rs_score=rs, advdec=ad, vol_ratio=vr, meta=meta)
                )
                created += int(is_new)
                updated += int(not is_new)

        self.stdout.write(self.style.SUCCESS(
            f"sector_load: created={created} updated={updated} file={path}"
        ))