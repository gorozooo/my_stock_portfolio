# management/commands/import_stockmaster.py
import csv
from django.core.management.base import BaseCommand
from stocks.models import StockMaster

class Command(BaseCommand):
    help = "東証公式CSVから銘柄マスタ（StockMaster）を更新・追加"

    def add_arguments(self, parser):
        parser.add_argument('csvfile', type=str, help="CSVファイルパス")

    def handle(self, *args, **options):
        path = options['csvfile']
        updated = 0
        created = 0
        with open(path, newline='', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                code = row.get('証券コード') or row.get('Code')
                name = row.get('銘柄名') or row.get('Name')
                sector = row.get('33業種') or row.get('Sector')
                if not (code and name and sector):
                    self.stdout.write(self.style.WARNING(f"不足: {row}"))
                    continue
                obj, created_flag = StockMaster.objects.update_or_create(
                    code=code.zfill(4),
                    defaults={'name': name.strip(), 'sector': sector.strip()}
                )
                if created_flag:
                    created += 1
                else:
                    updated += 1
        self.stdout.write(self.style.SUCCESS(f"作成 {created} 件、更新 {updated} 件"))
