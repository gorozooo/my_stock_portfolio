# portfolio/management/commands/import_stockmaster.py
import csv
import requests
from io import StringIO
from django.core.management.base import BaseCommand
from portfolio.models import StockMaster

JPX_CSV_URL = "https://www.jpx.co.jp/listing/stocks/new/csv/TSE_StockList.csv"

class Command(BaseCommand):
    help = "東証公式CSVから銘柄マスタ（StockMaster）を更新・追加"

    def add_arguments(self, parser):
        parser.add_argument(
            '--local',
            type=str,
            help="ローカルCSVファイルパス（指定すると自動ダウンロードせずにこれを使用）"
        )

    def handle(self, *args, **options):
        csv_path = options.get('local')

        if csv_path:
            self.stdout.write(self.style.NOTICE(f"ローカルCSVを使用: {csv_path}"))
            with open(csv_path, newline='', encoding='utf-8-sig') as f:
                csv_data = f.read()
        else:
            self.stdout.write(self.style.NOTICE(f"JPX公式CSVを自動ダウンロード中..."))
            try:
                r = requests.get(JPX_CSV_URL)
                r.raise_for_status()
                csv_data = r.content.decode("utf-8-sig")
            except Exception as e:
                self.stderr.write(self.style.ERROR(f"CSVダウンロード失敗: {e}"))
                return

        reader = csv.DictReader(StringIO(csv_data))
        updated = 0
        created = 0

        for row in reader:
            code = row.get('証券コード') or row.get('Code')
            name = row.get('銘柄名') or row.get('Name')
            sector = row.get('33業種') or row.get('Sector')
            if not (code and name):
                self.stdout.write(self.style.WARNING(f"不足: {row}"))
                continue

            obj, created_flag = StockMaster.objects.update_or_create(
                code=str(code).zfill(4),
                defaults={'name': name.strip(), 'sector': (sector or "").strip()}
            )
            if created_flag:
                created += 1
            else:
                updated += 1

        self.stdout.write(self.style.SUCCESS(f"作成 {created} 件、更新 {updated} 件"))