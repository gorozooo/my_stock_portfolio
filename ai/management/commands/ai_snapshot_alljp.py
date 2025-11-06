from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import pandas as pd
import datetime as dt
import time, re, requests
from lxml import html
import yfinance as yf


JPX_LIST_PAGE = "https://www.jpx.co.jp/markets/statistics-equities/misc/01.html"


def find_jpx_xls_url():
    """JPX公式ページから最新の上場銘柄一覧 .xls リンクを取得"""
    r = requests.get(JPX_LIST_PAGE, timeout=30)
    r.raise_for_status()
    doc = html.fromstring(r.content)
    links = doc.xpath("//a[@href]/@href")
    for href in links:
        if href.endswith(".xls") and "att" in href:
            if href.startswith("http"):
                return href
            return requests.compat.urljoin(JPX_LIST_PAGE, href)
    raise CommandError("JPXの.xlsリンクを見つけられませんでした。")


def download_xls(url: str, dst: Path):
    dst.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(url, timeout=60, stream=True) as r:
        r.raise_for_status()
        with open(dst, "wb") as f:
            for chunk in r.iter_content(8192):
                f.write(chunk)
    return dst


def read_jpx_list(xls_path: Path):
    """JPX Excelから 銘柄コード・銘柄名・33業種 を抽出"""
    df = pd.read_excel(xls_path, engine="xlrd")

    def norm(s):  # 列名のゆらぎ対策
        return str(s).strip().lower().replace("　", "").replace(" ", "")

    cols = {norm(c): c for c in df.columns}
    code_col = next((cols[k] for k in cols if "コード" in cols[k] or "銘柄コード" in cols[k]), None)
    name_col = next((cols[k] for k in cols if "銘柄名" in cols[k]), None)
    market_col = next((cols[k] for k in cols if "市場" in cols[k]), None)
    sector_col = next((cols[k] for k in cols if "33業種" in cols[k] or "業種分類" in cols[k]), None)

    if not (code_col and name_col and market_col):
        raise CommandError(f"列名解析失敗: {df.columns.tolist()}")

    # ETF/REIT等を除外
    mask_equity = ~df[market_col].astype(str).str.contains("ETF|ETN|REIT|投信|出資", regex=True)
    eq = df[mask_equity].copy()

    eq["code"] = eq[code_col].astype(str).str.extract(r"(\d{4})")[0]
    eq = eq.dropna(subset=["code"])
    eq["name"] = eq[name_col].astype(str).str.strip()
    eq["sector"] = eq[sector_col].astype(str).str.strip() if sector_col in eq.columns else ""
    eq = eq.drop_duplicates(subset=["code"])

    return eq[["code", "name", "sector"]]


def fetch_history(code: str, start: dt.datetime, end: dt.datetime, retries=3, pause=2.0):
    """Yahooから日足を取得（リトライ・バックオフ付き）"""
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
        time.sleep(pause * (2 ** i))
    return None


class Command(BaseCommand):
    help = "JPX公式Excelから日本株全銘柄(code,name,sector)を取得し、yfinanceで価格データを生成"

    def add_arguments(self, parser):
        parser.add_argument("--asof", default=None)
        parser.add_argument("--days", type=int, default=400)
        parser.add_argument("--limit", type=int, default=None)
        parser.add_argument("--workers", type=int, default=4)

    def handle(self, *args, **opts):
        asof = opts["asof"] or timezone.now().date().isoformat()
        end = dt.datetime.strptime(asof, "%Y-%m-%d")
        start = end - dt.timedelta(days=opts["days"])
        out_dir = Path(f"media/ohlcv/snapshots/{asof}")
        out_csv = out_dir / "ohlcv.csv"
        out_dir.mkdir(parents=True, exist_ok=True)

        # JPX Excel 取得
        self.stdout.write("[JPX] 最新Excelを確認中…")
        xls_url = find_jpx_xls_url()
        self.stdout.write(f"[JPX] URL: {xls_url}")
        tmp_xls = out_dir / "_jpx.xls"
        download_xls(xls_url, tmp_xls)

        eq = read_jpx_list(tmp_xls)
        if opts["limit"]:
            eq = eq.head(int(opts["limit"]))
        self.stdout.write(f"[JPX] 銘柄数: {len(eq)}")

        codes = eq["code"].tolist()
        meta = {r.code: (r.name, r.sector) for r in eq.itertuples(index=False)}

        results = []

        def job(code):
            df = fetch_history(code, start, end)
            if df is None or df.empty:
                return None
            name, sector = meta.get(code, ("", ""))
            df.insert(0, "code", code)
            df["name"] = name
            df["sector"] = sector
            return df

        self.stdout.write("[JPX] Yahoo Finance から価格取得中…")
        with ThreadPoolExecutor(max_workers=opts["workers"]) as ex:
            futs = {ex.submit(job, c): c for c in codes}
            for fut in as_completed(futs):
                res = fut.result()
                if res is not None:
                    results.append(res)

        if not results:
            raise CommandError("取得できた履歴が0件でした。レート制限の可能性あり。")

        out = pd.concat(results, ignore_index=True)
        out = out[["code", "date", "close", "volume", "name", "sector"]]
        out.to_csv(out_csv, index=False)

        self.stdout.write(self.style.SUCCESS(
            f"[JPX] 完了: 銘柄数={out['code'].nunique()} 行数={len(out)} → {out_csv}"
        ))