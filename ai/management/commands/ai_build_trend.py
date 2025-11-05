from django.core.management.base import BaseCommand
from ai.models import TrendResult
from pathlib import Path
import pandas as pd
from decimal import Decimal

class Command(BaseCommand):
    help = "Build TrendResult from snapshot CSVs"

    def add_arguments(self, parser):
        parser.add_argument("--root", type=str, required=True)
        parser.add_argument("--asof", type=str, required=True)

    def handle(self, *args, **opts):
        root = Path(opts["root"])
        asof = opts["asof"]
        csv_path = root / "ohlcv.csv"
        if not csv_path.exists():
            self.stderr.write(f"Snapshot not found: {csv_path}")
            return

        df = pd.read_csv(csv_path)
        df["code"] = df["code"].astype(str).apply(lambda x: x.split(".")[0])  # ← 重複対策
        latest = df.groupby("code").tail(1)

        TrendResult.objects.all().delete()
        objs = []
        for _, r in latest.iterrows():
            objs.append(
                TrendResult(
                    code=r["code"],
                    name=r["name"],
                    sector_jp=r["sector"],
                    last_price=Decimal(str(r["close"])),
                    rs_index=Decimal("0.50"),
                    slope=Decimal("0.00"),
                    confidence=Decimal("0.72"),  # 仮固定→後に自動計算
                    as_of=asof,
                )
            )
        TrendResult.objects.bulk_create(objs)
        self.stdout.write(f"Updated TrendResult: {len(objs)} items (as_of={asof})")