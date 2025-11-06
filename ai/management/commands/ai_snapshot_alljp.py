from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import pandas as pd
import datetime as dt
import time
import re
import requests
from lxml import html
import yfinance as yf
import xlrd  # ← 1.2.0 を直接使用（.xls 用）

JPX_LIST_PAGE = "https://www.jpx.co.jp/markets/statistics-equities/misc/01.html"

def find_jpx_xls_url():
    """JPXページから最新の .xls リンクを取得"""
    r = requests.get(JPX_LIST_PAGE, timeout=30)
    r.raise_for_status()
    doc = html.fromstring(r.content)
    for href in doc.xpath("//a[@href]/@href"):
        if href.endswith(".xls") and "att" in href:
            return href if href.startswith("http") else requests.compat.urljoin(JPX_LIST_PAGE, href)
    raise CommandError("JPXの.xlsリンクが見つかりませんでした。")

def download_xls(url: str, dst: Path):
    """xls を保存"""
    dst.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(url, timeout=60, stream=True) as r:
        r.raise_for_status()
        with open(dst, "wb") as f:
            for chunk in r.iter_content(8192):
                if chunk:
                    f.write(chunk)
    return dst

def _norm(s):
    return str(s).strip().lower().replace("　", "").replace(" ", "")

def read_jpx_list(xls_path: Path):
    """
    xlrd 1.2.0 で .xls を直接読む。
    個別株のみ抽出し DataFrame[code,name,sector] を返す。
    """
    book = xlrd.open_workbook(xls_path)
    sh = book.sheet_by_index(0)

    # 1行目（ヘッダ）を正規化
    headers = [sh.cell_value(0, c) for c in range(sh.ncols)]
    norm = [_norm(h) for h in headers]
    name_map = dict(zip(norm, headers))

    # 欲しい列名をゆるく同定
    def find_col(keys):
        for i, h in enumerate(headers):
            hh = str(h)
            if any(k in hh for k in keys):
                return i
        # 予備：正規化版から逆引き
        for i, nh in enumerate(norm):
            if any(k in nh for k in keys):
                return i
        return None

    col_code   = find_col(["コード", "銘柄コード", "code"])
    col_name   = find_col(["銘柄名", "名称", "name"])
    col_market = find_col(["市場", "市場・商品区分", "market"])
    col_sector = find_col(["33業種", "業種分類", "業種", "sector"])

    if col_code is None or col_name is None or col_market is None:
        raise CommandError(f"JPX列解析に失敗: {headers}")

    rows = []
    code_re = re.compile(r"(\d{4})")
    # 2行目以降がデータ
    for r in range(1, sh.nrows):
        code_raw = str(sh.cell_value(r, col_code))
        m = code_re.search(code_raw)
        if not m:
            continue
        code = m.group(1)

        name = str(sh.cell_value(r, col_name)).strip()
        market = str(sh.cell_value(r, col_market))

        # ETF/ETN/REIT/投信/インフラ 等は除外
        if re.search(r"ETF|ETN|REIT|投資信託|インフラ|出資", market):
            continue

        sector = ""
        if col_sector is not None:
            sector = str(sh.cell_value(r, col_sector)).strip()

        rows.append({"code": code, "name": name, "sector": sector})

    if not rows:
        raise CommandError("JPX .xls の解析結果が空です。")

    df = pd.DataFrame(rows, columns=["code", "name", "sector"]).drop_duplicates(subset=["code"])
    return df

def fetch_history(code: str, start: dt.datetime, end: dt.datetime, retries=3, pause=2.0):
    """yfinance で日足取得（リトライ＋バックオフ）"""
    sym = f"{code}.T"
    for i in range(retries + 1):
        try:
            df = yf.download(sym, start=start, end=end + dt.timedelta(days=1),
                             progress=False, auto_adjust=False, threads=False)
            if df is not None and not df.empty:
                df = df.rename(columns={"Close": "close", "Volume": "volume"}).reset_index()
                df["date"] = df["Date"].dt.strftime("%Y-%m-%d")
                return df[["date", "close", "volume"]]
        except Exception:
            pass
        time.sleep(pause * (2 ** i))  # 2,4,8...
    return None

class Command(BaseCommand):
    help = "JPX公式Excel→code/name/sector→yfinanceでOHLCV生成（CSV: code,date,close,volume,name,sector）"

    def add_arguments(self, p):
        p.add_argument("--asof", default=None)
        p.add_argument("--days", type=int, default=400)
        p.add_argument("--limit", type=int, default=None)    # テスト用（100→300→全件）
        p.add_argument("--workers", type=int, default=3)     # 429回避のため控えめ
        p.add_argument("--min", dest="min_code", type=int, default=None)  # 任意：コード帯
        p.add_argument("--max", dest="max_code", type=int, default=None)

    def handle(self, *args, **o):
        asof = o["asof"] or timezone.now().date().isoformat()
        end = dt.datetime.strptime(asof, "%Y-%m-%d")
        start = end - dt.timedelta(days=o["days"])
        out_dir = Path(f"media/ohlcv/snapshots/{asof}")
        out_dir.mkdir(parents=True, exist_ok=True)
        out_csv = out_dir / "ohlcv.csv"

        # 1) JPX Excel 取得
        self.stdout.write("[JPX] 最新Excelリンクを探索…")
        xls_url = find_jpx_xls_url()
        self.stdout.write(f"[JPX] {xls_url}")
        tmp_xls = out_dir / "_jpx_list.xls"
        download_xls(xls_url, tmp_xls)

        # 2) 個別株 master 作成
        eq = read_jpx_list(tmp_xls)

        # 任意：コード帯で絞る
        if o["min_code"] is not None or o["max_code"] is not None:
            lo = o["min_code"] if o["min_code"] is not None else 0
            hi = o["max_code"] if o["max_code"] is not None else 9999
            eq = eq[eq["code"].astype(int).between(lo, hi)]

        if o["limit"]:
            eq = eq.head(int(o["limit"]))

        codes = eq["code"].tolist()
        meta = {r.code: (r.name, r.sector) for r in eq.itertuples(index=False)}
        self.stdout.write(f"[JPX] 個別株（取得対象）: {len(codes)}")

        # 3) 価格取得（低並列）
        rows = []
        def task(code):
            df = fetch_history(code, start, end)
            if df is None or df.empty:
                return None
            name, sector = meta.get(code, ("", ""))
            df.insert(0, "code", code)
            df["name"] = name
            df["sector"] = sector
            return df

        self.stdout.write("[JPX] yfinance から日足取得中…")
        with ThreadPoolExecutor(max_workers=o["workers"]) as ex:
            futs = {ex.submit(task, c): c for c in codes}
            for fut in as_completed(futs):
                res = fut.result()
                if res is not None:
                    rows.append(res)

        if not rows:
            raise CommandError("取得できた履歴が0件でした。（429の可能性。--workers を下げる/--limit を使う）")

        out = pd.concat(rows, ignore_index=True)
        out = out[["code", "date", "close", "volume", "name", "sector"]]
        out.to_csv(out_csv, index=False)
        self.stdout.write(self.style.SUCCESS(
            f"[JPX] 完了: codes={out['code'].nunique()} rows={len(out)} -> {out_csv}"
        ))