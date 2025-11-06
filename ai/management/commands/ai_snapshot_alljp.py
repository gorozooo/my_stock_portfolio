from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import pandas as pd
import datetime as dt
import itertools
import math

import yfinance as yf
from yahooquery import Ticker

def enumerate_jp_symbols(min_code=1300, max_code=9999, batch=300, workers=8):
    """
    4桁コードを総当たりで 'XXXX.T' を作成し、yahooquery.Ticker.price で存在確認。
    戻り値: ['8035.T', '6758.T', ...]  ※EQUITYのみ採用
    """
    all_syms = [f"{i}.T" for i in range(min_code, max_code + 1)]
    valid = []

    def check_batch(chunk):
        t = Ticker(chunk, asynchronous=True, max_workers=workers, validate=True)
        prices = t.price or {}
        out = []
        for sym in chunk:
            p = prices.get(sym) or {}
            if (p.get("quoteType") == "EQUITY") and p.get("exchange") in {"TSE","JPX"}:
                out.append(sym)
        return out

    parts = range(0, len(all_syms), batch)
    with ThreadPoolExecutor(max_workers=max(2, workers//2)) as ex:
        futs = {ex.submit(check_batch, all_syms[i:i+batch]): i for i in parts}
        for fut in as_completed(futs):
            valid.extend(fut.result())
    if not valid:
        raise CommandError("日本株シンボルの列挙に失敗しました（検証ゼロ）。")
    return sorted(set(valid))

def fetch_meta(symbols, workers=12):
    """
    longName/sector/industry を一括取得。
    戻り値: { '8035': {'name':..., 'sector':..., 'industry':...}, ... }
    """
    t = Ticker(symbols, asynchronous=True, max_workers=workers, validate=True)
    profiles = t.asset_profile or {}
    prices   = t.price or {}
    meta = {}
    for sym in symbols:
        code = sym.replace(".T","")
        p = prices.get(sym) or {}
        prof = profiles.get(sym) or {}
        name = p.get("longName") or p.get("shortName") or ""
        sector = prof.get("sector") or ""
        industry = prof.get("industry") or ""
        meta[code] = {"name": name, "sector": sector or industry, "industry": industry}
    return meta

def fetch_history(symbol, start, end):
    df = yf.download(symbol, start=start, end=end + dt.timedelta(days=1), progress=False, auto_adjust=False, threads=False)
    if df is None or df.empty:
        return None
    df = df.rename(columns={"Close":"close","Volume":"volume"}).reset_index()
    df["date"] = df["Date"].dt.strftime("%Y-%m-%d")
    return df[["date","close","volume"]]

class Command(BaseCommand):
    help = "日本株・全上場を外部から取得（名称/業種付き）し、OHLCVスナップショット生成（並列対応、CSV不要）"

    def add_arguments(self, p):
        p.add_argument("--asof", default=None)
        p.add_argument("--days", type=int, default=420)
        p.add_argument("--workers", type=int, default=12)
        p.add_argument("--batch", type=int, default=300)  # 検証時のpriceバッチ
        p.add_argument("--min", dest="min_code", type=int, default=1300)
        p.add_argument("--max", dest="max_code", type=int, default=9999)
        p.add_argument("--limit", type=int, default=None)  # デバッグ用

    def handle(self, *args, **o):
        asof = o["asof"] or timezone.now().date().isoformat()
        end = dt.datetime.strptime(asof, "%Y-%m-%d")
        start = end - dt.timedelta(days=o["days"]*2)

        out_dir = Path(f"media/ohlcv/snapshots/{asof}")
        out_dir.mkdir(parents=True, exist_ok=True)
        out_csv = out_dir / "ohlcv.csv"

        self.stdout.write(f"[alljp] enumerate & validate symbols…")
        symbols = enumerate_jp_symbols(
            min_code=o["min_code"], max_code=o["max_code"],
            batch=o["batch"], workers=o["workers"]
        )
        if o["limit"]:
            symbols = symbols[:o["limit"]]
        self.stdout.write(f"[alljp] valid symbols: {len(symbols)}")

        self.stdout.write(f"[alljp] fetch meta (name/sector)…")
        meta = fetch_meta(symbols, workers=o["workers"])

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
                    m = meta.get(code, {})
                    df.insert(0, "code", code)
                    df["name"] = m.get("name","")
                    # sectorが空ならindustryで補完
                    sector = m.get("sector") or m.get("industry") or ""
                    df["sector"] = sector
                    rows.append(df)
                except Exception as e:
                    self.stderr.write(f"[skip] {sym}: {e}")

        if not rows:
            raise CommandError("取得できた履歴が0件でした。")

        out = pd.concat(rows, ignore_index=True)
        out = out[["code","date","close","volume","name","sector"]]
        out.to_csv(out_csv, index=False)
        self.stdout.write(self.style.SUCCESS(
            f"[alljp] done: codes={out['code'].nunique()} rows={len(out)} out={out_csv}"
        ))