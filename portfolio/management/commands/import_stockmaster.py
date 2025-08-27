# import_stockmaster.py
import pandas as pd
from django.core.management.base import BaseCommand
from portfolio.models import StockMaster

class Command(BaseCommand):
    help = 'ExcelファイルからStockMasterを更新/追加します'

    def add_arguments(self, parser):
        parser.add_argument('--file', type=str, help='読み込むExcelファイルのパス')

    def handle(self, *args, **options):
        file_path = options.get('file')
        if not file_path:
            self.stdout.write(self.style.ERROR('ファイルパスを指定してください'))
            return

        try:
            # Excel読み込み（文字化け対策）
            df = pd.read_excel(file_path, dtype=str)
            df = df.fillna("")  # NaN を空文字に置換

            # 列名の全角スペースや不可視文字を削除
            df.columns = [c.strip().replace("\u3000", "") for c in df.columns]

            count = 0
            for _, row in df.iterrows():
                code = row['コード'].strip()
                name = row['銘柄名'].strip()
                sector = row.get('33業種区分', '').strip()  # セクター列を取得

                if not code or not name:
                    continue

                stock, created = StockMaster.objects.update_or_create(
                    code=code,
                    defaults={'name': name, 'sector': sector}
                )
                count += 1

            self.stdout.write(self.style.SUCCESS(f'{count}件の銘柄を更新/追加しました'))

        except Exception as e:
            self.stdout.write(self.style.ERROR(f'Excel 読み込み失敗: {e}'))