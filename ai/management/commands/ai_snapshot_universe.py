from django.core.management.base import BaseCommand, CommandError
from django.core.management import call_command
from django.utils import timezone
from pathlib import Path
import csv

class Command(BaseCommand):
    help = "Build OHLCV snapshot for a universe of codes by orchestrating ai_snapshot_ohlcv."

    def add_arguments(self, parser):
        parser.add_argument("--universe", choices=["PRIME","STANDARD","N225","WATCH","HOLDINGS"], default=None)
        parser.add_argument("--codes", help="Path or comma/line separated codes.", default=None)
        parser.add_argument("--limit", type=int, default=None)
        parser.add_argument("--asof", default=None)
        parser.add_argument("--output", default=None)

    def handle(self, *args, **opts):
        asof = opts["asof"] or timezone.now().date().isoformat()
        out = opts["output"] or f"media/ohlcv/snapshots/{asof}/ohlcv.csv"
        Path(Path(out).parent).mkdir(parents=True, exist_ok=True)

        # --- resolve codes ---
        codes = []
        if opts["codes"]:
            txt = opts["codes"]
            p = Path(txt)
            if p.exists():
                txt = p.read_text(encoding="utf-8", errors="ignore")
            raw = []
            for line in txt.replace(",", "\n").splitlines():
                c = line.strip()
                if not c or c.lower().startswith("code"):
                    continue
                if "," in c:
                    c = c.split(",")[0].strip()
                raw.append(c)
            codes = [c for c in raw if c and c.lower() != "nan"]
        elif opts["universe"]:
            uni = opts["universe"]
            if uni in ("PRIME","STANDARD"):
                from master.models import StockMaster as M
                qs = M.objects.filter(market=uni).order_by("code").values_list("code", flat=True)
                codes = list(qs)
            elif uni == "N225":
                from master.models import StockMaster as M
                qs = M.objects.filter(indexes__icontains="NIKKEI225").order_by("code").values_list("code", flat=True)
                codes = list(qs)
            elif uni == "WATCH":
                from watch.models import Watch
                codes = list(Watch.objects.order_by("code").values_list("code", flat=True))
            elif uni == "HOLDINGS":
                from holdings.models import Holding
                codes = list(Holding.objects.order_by("code").values_list("code", flat=True))
        else:
            raise CommandError("Specify --universe or --codes")

        if not codes:
            raise CommandError("No codes resolved for snapshot.")

        if opts["limit"]:
            codes = codes[:opts["limit"]]

        # --- init output with header ---
        with open(out, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["code","date","close","volume","name","sector"])

        ok = 0; bad = 0
        total = len(codes)
        for i, code in enumerate(codes, 1):
            code = str(code).strip()
            if not code or code.lower() == "nan":
                self.stderr.write(self.style.WARNING(f"[skip] nan code"))
                bad += 1
                continue
            self.stdout.write(f"[{i}/{total}] snapshot {code}")
            try:
                call_command("ai_snapshot_ohlcv", code=code, asof=asof, append=out, verbosity=0)
                ok += 1
            except Exception as e:
                bad += 1
                self.stderr.write(self.style.ERROR(f"  -> fail {code}: {e}"))

        self.stdout.write(self.style.SUCCESS(f"Done: ok={ok} bad={bad} out={out}"))