# portfolio/management/commands/import_stockmaster.py
import csv
from django.core.management.base import BaseCommand
from portfolio.models import StockMaster

class Command(BaseCommand):
    help = "東証公式CSVから銘柄マスタ（StockMaster）を更新・追加"

    def add_arguments(self, parser):
        parser.add_argument('csvfile', type=str, help="CSVファイルパス")

    def handle(self, *args, **options):
        path = options['csvfile']
        updated_count = 0
        created_count = 0

        # utf-8-sig で BOM 対応
        with open(path, newline='', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            for row in reader:
                code = (row.get('証券コード') or row.get('Code') or "").strip()
                name = (row.get('銘柄名') or row.get('Name') or "").strip()
                sector = (row.get('33業種') or row.get('Sector') or "").strip()

                if not (code and name):
                    self.stdout.write(self.style.WARNING(f"不足データ: {row}"))
                    continue

                # code を 4 桁にゼロ埋め
                code = code.zfill(4)

                obj, created_flag = StockMaster.objects.update_or_create(
                    code=code,
                    defaults={
                        'name': name,
                        'sector': sector
                    }
                )

                if created_flag:
                    created_count += 1
                else:
                    updated_count += 1

        self.stdout.write(self.style.SUCCESS(f"作成 {created_count} 件、更新 {updated_count} 件"))