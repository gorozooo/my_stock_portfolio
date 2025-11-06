from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import pandas as pd
import datetime as dt
import time

import yfinance as yf
from yahooquery import Screener, Ticker

def fetch_all_jp_symbols():
    """
    Yahoo Finance screener から日本上場銘柄(末尾 .T)を全件取得
    ※ページング対応。戻り値: ["8035.T", "6758.T", ...]
    """
    scr = Screener()
    symbols = []
    # 日本株のプリセットスクリーンを使う（region=jp, exchange=TSE相当）
    # 近いプリセット: 'most_actives_japan' 等。ただし全件化のためカスタムでページング。
    # yahooqueryのscreener.get_screenersでクエリ指定
    query = {
        "offset": 0,
        "size": 250,
        "sortField": "symbol",
        "sortType": "asc",
        "quoteType": "EQUITY",
        "query": {
            "operator": "and",
            "operands": [
                {"operator": "or", "operands": [
                    {"operator": "equals", "operands": ["region", "jp"]}
                ]},
                {"operator": "or", "operands": [
                    {"operator": "contains", "operands": ["exchange", "TSE"]},
                    {"operator": "contains", "operands": ["shortName", ""]}  # 緩めに拾う
                ]}
            ]
        }
    }

    seen = set()
    while True:
        data = scr.screener(query)
        quotes = (data or {}).get("quotes") or []
        got = 0
        for q in quotes:
            sym = q.get("symbol") or ""
            if sym.endswith(".T") and sym not in seen:
                seen.add(sym)
                symbols.append(sym)
                got += 1
        if got < query["size"]:
            break
        query["offset"] += query["size"]
        time.sleep(0.2)
    if not symbols:
        raise CommandError("日本株シンボルが取得できませんでした（Yahoo Screener）。")
    return symbols

def enrich_meta(symbols):
    """
    銘柄メタデータ取得: name, sector, industry
    戻り値: { '8035': {'name':'東京エレクトロン','sector':'半導体','industry':'Semiconductors'} , ... }
    """
    # yahooqueryのTickerでまとめて取得
    t = Ticker(symbols, asynchronous=True, max_workers=8, validate=True)
    profiles = t.asset_profile or {}
    prices   = t.price or {}
    meta = {}
    for sym in symbols:
        code = sym.replace(".T", "")
        p = prices.get(sym) or {}
        prof = profiles.get(sym) or {}
        name = p.get("longName") or p.get("shortName") or prof.get("longBusinessSummary") or ""
        sector = prof.get("sector") or ""
        industry = prof.get("industry") or ""
        meta[code] = {"name": name, "sector": sector or industry, "industry": industry}
    return meta

def fetch_history(symbol, start, end):
    """
    yfinanceで履歴取得 → DataFrame[date, close, volume]
    symbol は '8035.T' 形式
    """
    df = yf.download(symbol, start=start, end=end, progress=False, auto_adjust=False, threads=False)
    if df is None or df.empty:
        return None
    df = df.rename(columns={"Close":"close","Volume":"volume"})
    df = df.reset_index()
    df["date"] = df["Date"].dt.strftime("%Y-%m-%d")
    return df[["date","close","volume"]]

class Command(BaseCommand):
    help = "日本株・全上場を外部から取得し、名称/業種付きでOHLCVスナップショットを生成（並列対応）"

    def add_arguments(self, p):
        p.add_argument("--asof", default=None, help="YYYY-MM-DD（省略時は今日）")
        p.add_argument("--days", type=int, default=420, help="履歴日数の目安（営業日×2バッファ）")
        p.add_argument("--limit", type=int, default=None, help="検証用に上限件数を絞る（本番は未指定）")
        p.add_argument("--workers", type=int, default=8, help="並列ワーカー数（サーバ性能に合わせて）")

    def handle(self, *args, **o):
        asof = o["asof"] or timezone.now().date().isoformat()
        end = dt.datetime.strptime(asof, "%Y-%m-%d")
        start = end - dt.timedelta(days=o["days"]*2)  # 休日バッファ

        out_dir = Path(f"media/ohlcv/snapshots/{asof}")
        out_dir.mkdir(parents=True, exist_ok=True)
        out_csv = out_dir / "ohlcv.csv"

        self.stdout.write(f"[alljp] enumerate symbols…")
        symbols = fetch_all_jp_symbols()  # ['8035.T', ...]
        symbols = sorted(set(symbols))
        if o["limit"]:
            symbols = symbols[:o["limit"]]
        self.stdout.write(f"[alljp] total symbols: {len(symbols)}")

        self.stdout.write(f"[alljp] fetch meta (name/sector)…")
        meta = enrich_meta(symbols)

        self.stdout.write(f"[alljp] fetch histories in parallel (workers={o['workers']})…")
        rows = []
        with ThreadPoolExecutor(max_workers=o["workers"]) as ex:
            futs = {ex.submit(fetch_history, sym, start, end): sym for sym in symbols}
            for fut in as_completed(futs):
                sym = futs[fut]
                code = sym.replace(".T","")
                try:
                    df = fut.result()
                    if df is None or df.empty:
                        continue
                    name = (meta.get(code) or {}).get("name","")
                    sector = (meta.get(code) or {}).get("sector","")
                    df.insert(0, "code", code)
                    df["name"] = name
                    df["sector"] = sector
                    rows.append(df)
                except Exception as e:
                    self.stderr.write(f"[skip] {sym}: {e}")

        if not rows:
            raise CommandError("有効なデータが1件も取得できませんでした。")

        out = pd.concat(rows, ignore_index=True)
        out = out[["code","date","close","volume","name","sector"]]
        out.to_csv(out_csv, index=False)
        self.stdout.write(self.style.SUCCESS(f"[alljp] done: codes={out['code'].nunique()} rows={len(out)} out={out_csv}"))