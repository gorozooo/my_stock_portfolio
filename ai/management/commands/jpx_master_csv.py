from django.core.management.base import BaseCommand, CommandError
from pathlib import Path
import requests, xlrd, csv
from lxml import html

JPX_LIST_PAGE = "https://www.jpx.co.jp/markets/statistics-equities/misc/01.html"

def find_jpx_xls_url():
    r = requests.get(JPX_LIST_PAGE, timeout=30); r.raise_for_status()
    doc = html.fromstring(r.content)
    for href in doc.xpath("//a[@href]/@href"):
        if href.endswith(".xls") and "att" in href:
            return href if href.startswith("http") else requests.compat.urljoin(JPX_LIST_PAGE, href)
    raise CommandError("JPXの.xlsリンクが見つかりません。")

def download_xls(url: str, dst: Path):
    dst.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(url, stream=True, timeout=60) as r:
        r.raise_for_status()
        with open(dst, "wb") as f:
            for chunk in r.iter_content(8192):
                if chunk: f.write(chunk)
    return dst

def norm(s: str) -> str:
    return str(s).strip().lower().replace("　","").replace(" ","")

class Command(BaseCommand):
    help = "JPX公式Excelから code,name,sector をCSVに出力（ETF/REIT/投信は除外）"

    def add_arguments(self, p):
        p.add_argument("--out", required=True, help="出力先CSVパス（例: media/jpx_master.csv）")

    def handle(self, *args, **o):
        out = Path(o["out"])
        xls_url = find_jpx_xls_url()
        tmp_xls = out.with_suffix(".xls")
        self.stdout.write(f"[JPX] {xls_url}")
        download_xls(xls_url, tmp_xls)

        book = xlrd.open_workbook(tmp_xls)
        sh = book.sheet_by_index(0)
        headers = [sh.cell_value(0, c) for c in range(sh.ncols)]
        nheaders = [norm(h) for h in headers]

        def fidx(keys):
            for i,h in enumerate(headers):
                if any(k in str(h) for k in keys): return i
            for i,nh in enumerate(nheaders):
                if any(k in nh for k in keys): return i
            return None

        i_code   = fidx(["コード","銘柄コード","code"])
        i_name   = fidx(["銘柄名","名称","name"])
        i_market = fidx(["市場","市場・商品区分","market"])
        i_sector = fidx(["33業種","業種分類","業種","sector"])

        if i_code is None or i_name is None or i_market is None:
            raise CommandError(f"列検出失敗: {headers}")

        rows = []
        import re
        for r in range(1, sh.nrows):
            code_raw = str(sh.cell_value(r, i_code))
            m = re.search(r"(\d{4})", code_raw)
            if not m: continue
            code = m.group(1)
            name = str(sh.cell_value(r, i_name)).strip()
            market = str(sh.cell_value(r, i_market))
            if re.search(r"ETF|ETN|REIT|投資信託|インフラ|出資", market):  # 非・個別株を除外
                continue
            sector = str(sh.cell_value(r, i_sector)).strip() if i_sector is not None else ""
            rows.append((code, name, sector))

        if not rows:
            raise CommandError("抽出結果が0件でした。")
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["code","name","sector"])
            w.writerows(rows)
        self.stdout.write(self.style.SUCCESS(f"[JPX] CSV出力: {out} / codes={len(rows)}"))