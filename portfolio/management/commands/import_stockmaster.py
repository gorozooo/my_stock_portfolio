# portfolio/management/commands/import_stockmaster.py
import requests
import pandas as pd
from io import BytesIO
from django.core.management.base import BaseCommand
from portfolio.models import StockMaster

# JPXの Excel（公式ページが変わることがあるので変更があればここを更新）
JPX_XLS_URL = "https://www.jpx.co.jp/markets/statistics-equities/misc/tse-listed-issues.xlsx"

class Command(BaseCommand):
    help = "JPX公式Excelから銘柄マスタ（StockMaster）を更新・追加"

    def add_arguments(self, parser):
        parser.add_argument(
            '--local',
            type=str,
            help="ローカルExcelファイルパス（指定すると自動ダウンロードではなくこれを使用）"
        )

    def handle(self, *args, **options):
        local_path = options.get('local')

        if local_path:
            self.stdout.write(self.style.NOTICE(f"ローカルExcelを使用: {local_path}"))
            try:
                df = pd.read_excel(local_path, sheet_name=0)
            except Exception as e:
                self.stderr.write(self.style.ERROR(f"ローカルExcel読み込み失敗: {e}"))
                return
        else:
            self.stdout.write("JPX公式Excelを自動ダウンロード中...")
            try:
                resp = requests.get(JPX_XLS_URL, timeout=30)
                resp.raise_for_status()
            except requests.RequestException as e:
                self.stderr.write(self.style.ERROR(f"Excelダウンロード失敗: {e}"))
                return

            try:
                xls = BytesIO(resp.content)
                df = pd.read_excel(xls, sheet_name=0)
            except Exception as e:
                self.stderr.write(self.style.ERROR(f"Excel読み込み失敗: {e}"))
                return

        # --- カラム名の正規化（Excelの列名が変わっても対応しやすく） ---
        # 代表的な日本語・英語ヘッダ候補を用意して、見つかった列名で正規化する
        colmap = {}
        cols = list(df.columns)
        # print(cols) -> デバッグ用

        # code
        for candidate in ('証券コード', 'コード', 'Code', 'コード（銘柄コード）', '銘柄コード'):
            for c in cols:
                if candidate == c:
                    colmap['code'] = c
                    break
            if 'code' in colmap:
                break
        # name
        for candidate in ('銘柄名', '会社名', 'Name'):
            for c in cols:
                if candidate == c:
                    colmap['name'] = c
                    break
            if 'name' in colmap:
                break
        # sector
        for candidate in ('33業種', '33業種区分', '業種', 'Sector'):
            for c in cols:
                if candidate == c:
                    colmap['sector'] = c
                    break
            if 'sector' in colmap:
                break

        # 必要最低限のカラムがない場合は警告して中止
        if 'code' not in colmap or 'name' not in colmap:
            self.stderr.write(self.style.ERROR(
                "Excelに必要な列（証券コード/銘柄名）が見つかりません。カラム: " + ",".join(cols)
            ))
            return

        created = 0
        updated = 0

        for _, row in df.iterrows():
            raw_code = row.get(colmap['code'])
            if pd.isna(raw_code):
                continue
            code = str(int(raw_code)) if (isinstance(raw_code, (int, float)) and not pd.isna(raw_code)) else str(raw_code).strip()
            code = code.zfill(4)  # 4桁ゼロ埋め

            name = str(row.get(colmap['name']) or "").strip()
            sector = ""
            if 'sector' in colmap:
                sector = str(row.get(colmap['sector']) or "").strip()

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