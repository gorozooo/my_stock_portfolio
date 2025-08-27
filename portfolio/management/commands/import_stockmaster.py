# portfolio/management/commands/import_stockmaster.py
import pandas as pd
from django.core.management.base import BaseCommand
from portfolio.models import StockMaster

class Command(BaseCommand):
    help = "Excel から StockMaster を更新/追加（銘柄名 + セクター）"

    def add_arguments(self, parser):
        parser.add_argument(
            '--file',
            type=str,
            default='tse-listed-issues.xls',
            help='読み込む Excel ファイルのパス'
        )

    def handle(self, *args, **options):
        file_path = options['file']

        # Excel 読み込み
        try:
            df = pd.read_excel(file_path)
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Excel 読み込み失敗: {e}"))
            return

        count = 0
        for _, row in df.iterrows():
            code = str(row['コード']).strip()  # 証券コード
            name = str(row['銘柄名']).strip()   # 銘柄名
            sector = str(row['33業種区分']).strip()  # セクター（6列目）

            if not code or not name:
                continue

            obj, created = StockMaster.objects.update_or_create(
                code=code,
                defaults={
                    'name': name,
                    'sector': sector,
                }
            )
            count += 1

        self.stdout.write(self.style.SUCCESS(f"{count} 件の銘柄を更新/追加しました"))