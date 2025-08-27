from django.core.management.base import BaseCommand
import pandas as pd
from portfolio.models import StockMaster

class Command(BaseCommand):
    help = "東証上場銘柄リストを読み込み、StockMaster を更新/追加する"

    def add_arguments(self, parser):
        parser.add_argument("--file", type=str, required=True, help="Excelファイルのパス")

    def handle(self, *args, **options):
        file_path = options["file"]

        try:
            df = pd.read_excel(file_path, dtype=str)
        except Exception as e:
            self.stderr.write(self.style.ERROR(f"Excel 読み込み失敗: {e}"))
            return

        count = 0
        for _, row in df.iterrows():
            code = str(row["コード"]).strip()
            name = str(row["銘柄名"]).strip()
            sector = str(row["33業種区分"]).strip()  # ✅ ここで業種を取得

            if not code.isdigit() or not name:
                continue

            StockMaster.objects.update_or_create(
                code=code,
                defaults={
                    "name": name,
                    "sector": sector,
                },
            )
            count += 1

        self.stdout.write(self.style.SUCCESS(f"{count}件の銘柄を更新/追加しました"))
