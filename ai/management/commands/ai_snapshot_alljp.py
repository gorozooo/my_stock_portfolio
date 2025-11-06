from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import pandas as pd
import datetime as dt
import time
import requests
from lxml import html
import yfinance as yf

# JPX 公式の「東証上場銘柄一覧」ページ（毎月更新）
JPX_LIST_PAGE = "https://www.jpx.co.jp/markets/statistics-equities/misc/01.html"

def find_jpx_xls_url():
    """JPXページから最新の .xls リンクを1つ取得"""
    r = requests.get(JPX_LIST_PAGE, timeout=30)
    r.raise_for_status()
    doc = html.fromstring(r.content)
    for href in doc.xpath("//a[@href]/@href"):
        if href.endswith(".xls") and "att" in href:
            return href if href.startswith("http") else requests.compat.urljoin(JPX_LIST_PAGE, href)
    raise CommandError("JPXの.xlsリンクが見つかりませんでした。")

def download_xls(url: str, dst: Path):
    """xls を保存（再利用できるよう media 配下に置く）"""
    dst.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(url, timeout=60, stream=True) as r:
        r.raise_for_status()
        with open(dst, "wb") as f:
            for chunk in r.iter_content(8192):
                if chunk:
                    f.write(chunk)
    return dst

def read_jpx_list(xls_path: Path):
    """JPX xls から 個別株のみ抽出し code/name/sector を返す"""
    # xls 読み取りには xlrd==1.2.0 が必要
    df = pd.read_excel(xls_path, engine="xlrd")

    def norm(s):  # 列名のゆらぎ吸収
        return str(s).strip().lower().replace("　", "").replace(" ", "")

    cols = {norm(c): c for c in df.columns}
    code_col   = next((cols[k] for k in cols if "コード" in cols[k] or "銘柄コード" in cols[k]), None)
    name_col   = next((cols[k] for k in cols if "銘柄名" in cols[k]), None)
    market_col = next((cols[k] for k in cols if "市場" in cols[k] or "市場・商品区分" in cols[k]), None)
    sector_col = next((cols[k] for k in cols if "33業種" in cols[k] or "業種分類" in cols[k] or cols[k]=="業種"), None)
    if not (code_col and name_col and market_col):
        raise CommandError(f"JPX列解析に失敗: {df.columns.tolist()}")

    # ETF/ETN/REIT/投信/インフラなど非・個別株を除外
    mask_eq = ~df[market_col].astype(str).str.contains("ETF|ETN|REIT|投資信託|インフラ|出資", regex=True)
    eq = df[mask_eq].copy()

    # 4桁コード抽出
    eq["code"] = eq[code_col].astype(str).str.extract(r"(\d{4})")[0]
    eq = eq.dropna(subset=["code"]).drop_duplicates(subset=["code"])
    eq["name"] = eq[name_col].astype(str).str.strip()
    eq["sector"] = eq[sector_col].astype(str).str.strip() if sector_col in eq.columns else ""

    return eq[["code", "name", "sector"]]

def fetch_history(code: str, start: dt.datetime, end: dt.datetime, retries=3, pause=2.0):
    """yfinance で日足取得（レート制限想定のリトライ＆バックオフ）"""
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
        time.sleep(pause * (2 ** i))  # 2,4,8...秒
    return None

class Command(BaseCommand):
    help = "JPX公式Excelから code/name/sector を取り、yfinanceでOHLCVを生成（CSV: code,date,close,volume,name,sector）"

    def add_arguments(self, p):
        p.add_argument("--asof", default=None)
        p.add_argument("--days", type=int, default=400)      # 履歴日数（営業日換算で約1年強）
        p.add_argument("--limit", type=int, default=None)    # テスト用件数（100→300→全件）
        p.add_argument("--workers", type=int, default=3)     # 429回避のため控えめデフォルト
        p.add_argument("--min", dest="min_code", type=int, default=None)  # コード帯で絞り込む（任意）
        p.add_argument("--max", dest="max_code", type=int, default=None)

    def handle(self, *args, **o):
        asof = o["asof"] or timezone.now().date().isoformat()
        end = dt.datetime.strptime(asof, "%Y-%m-%d")
        start = end - dt.timedelta(days=o["days"])
        out_dir = Path(f"media/ohlcv/snapshots/{asof}")
        out_dir.mkdir(parents=True, exist_ok=True)
        out_csv = out_dir / "ohlcv.csv"

        # 1) JPX Excel ダウンロード
        self.stdout.write("[JPX] 最新Excelリンクを探索…")
        xls_url = find_jpx_xls_url()
        self.stdout.write(f"[JPX] {xls_url}")
        tmp_xls = out_dir / "_jpx_list.xls"
        download_xls(xls_url, tmp_xls)

        # 2) 個別株 master を作成（code/name/sector）
        eq = read_jpx_list(tmp_xls)

        # 任意：コード帯で絞る（ETF帯を避けたい等）
        if o["min_code"] is not None or o["max_code"] is not None:
            lo = o["min_code"] if o["min_code"] is not None else 0
            hi = o["max_code"] if o["max_code"] is not None else 9999
            eq = eq[eq["code"].astype(int).between(lo, hi)]

        if o["limit"]:
            eq = eq.head(int(o["limit"]))

        codes = eq["code"].tolist()
        meta = {r.code: (r.name, r.sector) for r in eq.itertuples(index=False)}
        self.stdout.write(f"[JPX] 個別株（取得対象）: {len(codes)}")

        # 3) 価格取得（低並列・バックオフ）
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
            raise CommandError("取得できた履歴が0件でした。（レート制限の可能性。--workers を下げる/--limit を使う）")

        out = pd.concat(rows, ignore_index=True)
        out = out[["code", "date", "close", "volume", "name", "sector"]]
        out.to_csv(out_csv, index=False)
        self.stdout.write(self.style.SUCCESS(
            f"[JPX] 完了: codes={out['code'].nunique()} rows={len(out)} -> {out_csv}"
        ))