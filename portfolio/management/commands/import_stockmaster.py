import requests
import pandas as pd
from io import BytesIO
from django.core.management.base import BaseCommand
from portfolio.models import StockMaster

JPX_XLS_URL = "https://www.jpx.co.jp/markets/statistics-equities/misc/tse-listed-issues.xlsx"

class Command(BaseCommand):
    help = "JPX公式Excelから銘柄マスタ（StockMaster）を更新・追加"

    def handle(self, *args, **options):
        self.stdout.write("JPX公式Excelを自動ダウンロード中...")
        try:
            resp = requests.get(JPX_XLS_URL)
            resp.raise_for_status()
        except requests.RequestException as e:
            self.stdout.write(self.style.ERROR(f"Excelダウンロード失敗: {e}"))
            return

        xls = BytesIO(resp.content)
        df = pd.read_excel(xls, sheet_name=0)

        # Excelの列名に応じて調整
        # 例: 'コード', '銘柄名', '33業種区分'
        df = df.rename(columns={
            'コード': 'code',
            '銘柄名': 'name',
            '33業種区分': 'sector'
        })

        updated = 0
        created = 0
        for _, row in df.iterrows():
            code = str(row.get('code')).zfill(4)
            name = str(row.get('name')).strip()
            sector = str(row.get('sector')).strip() if row.get('sector') else ""

            if not code or not name:
                continue

            obj, created_flag = StockMaster.objects.update_or_create(
                code=code,
                defaults={'name': name, 'sector': sector}
            )
            if created_flag:
                created += 1
            else:
                updated += 1

        self.stdout.write(self.style.SUCCESS(f"作成 {created} 件、更新 {updated} 件"))