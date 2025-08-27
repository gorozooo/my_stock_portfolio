# portfolio/management/commands/import_stockmaster_manual.py
import xlrd
from django.core.management.base import BaseCommand
from portfolio.models import StockMaster

class Command(BaseCommand):
    help = "手動でダウンロードしたJPX上場銘柄Excel(.xls)を取り込みます"

    def add_arguments(self, parser):
        parser.add_argument(
            "filepath",
            type=str,
            help="Excelファイルのパス（例: tse-listed-issues.xls）"
        )

    def handle(self, *args, **options):
        filepath = options["filepath"]
        try:
            wb = xlrd.open_workbook(filepath)
        except Exception as e:
            self.stderr.write(f"Excelファイルを開けません: {e}")
            return

        ws = wb.sheet_by_index(0)
        count = 0

        for row_idx in range(1, ws.nrows):
            row = ws.row(row_idx)
            code = str(int(row[0].value)).zfill(4) if row[0].value else None
            name = row[1].value if len(row) > 1 else None
            sector = row[2].value if len(row) > 2 else ""

            if not code or not name:
                continue

            StockMaster.objects.update_or_create(
                code=code,
                defaults={"name": name, "sector": sector}
            )
            count += 1

        self.stdout.write(self.style.SUCCESS(f"{count}件の銘柄を更新/追加しました"))
