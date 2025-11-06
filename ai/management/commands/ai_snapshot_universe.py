from django.core.management.base import BaseCommand, CommandError
from django.core.management import call_command
from django.utils import timezone
from pathlib import Path
import csv

class Command(BaseCommand):
    help = "複数銘柄のOHLCVスナップショットを一括生成する"

    def add_arguments(self, parser):
        parser.add_argument("--codes", help="コードリスト（カンマまたは改行区切り）", required=True)
        parser.add_argument("--asof", help="日付 YYYY-MM-DD", default=None)
        parser.add_argument("--output", help="出力先", default=None)

    def handle(self, *args, **opts):
        asof = opts["asof"] or timezone.now().date().isoformat()
        out = opts["output"] or f"media/ohlcv/snapshots/{asof}/ohlcv.csv"
        Path(Path(out).parent).mkdir(parents=True, exist_ok=True)
        codes = [c.strip() for c in opts["codes"].replace(",", "\n").split() if c.strip()]
        if not codes:
            raise CommandError("コードが指定されていません")

        # 初期化
        with open(out, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["code","date","close","volume","name","sector"])

        ok = 0
        for i, code in enumerate(codes, 1):
            self.stdout.write(f"[{i}/{len(codes)}] snapshot {code}")
            try:
                call_command("ai_snapshot_ohlcv", code=code, asof=asof, append=out, verbosity=0)
                ok += 1
            except Exception as e:
                self.stderr.write(f"  -> fail {code}: {e}")

        self.stdout.write(self.style.SUCCESS(f"完了: {ok}銘柄 out={out}"))