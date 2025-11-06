from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import pandas as pd
import datetime as dt

import yfinance as yf
from yahooquery import Ticker

# --- 存在判定を yfinance で行う（型崩れのない確実ルート） ----------------
def _exists_on_tse(sym: str) -> bool:
    """
    yfinance の超軽量ヒストリで存在確認。
    sym: '8035.T' 形式。過去5日で1本でも返れば上場とみなす。
    """
    try:
        hist = yf.Ticker(sym).history(period="5d", interval="1d")
        return hist is not None and not hist.empty
    except Exception:
        return False

def enumerate_jp_symbols(min_code=1300, max_code=9999, workers=12):
    """
    4桁コードを総当たりで 'XXXX.T' を作り、yfinanceで存在判定。
    """
    candidates = [f"{i}.T" for i in range(min_code, max_code + 1)]
    valid = []

    def check(sym):
        return sym if _exists_on_tse(sym) else None

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(check, s): s for s in candidates}
        for fut in as_completed(futs):
            ok = fut.result()
            if ok:
                valid.append(ok)

    if not valid:
        raise CommandError("日本株シンボルの列挙に失敗しました（該当ゼロ）。")
    return sorted(set(valid))

# --- メタ（名称・セクター）取得：yahooquery（型崩れガード） -------------
def fetch_meta(symbols, workers=12):
    """
    longName/shortName, sector/industry を取得し、
    sector が空なら industry で補完。
    戻: {'8035': {'name': '東京エレクトロン', 'sector': 'Semiconductors', 'industry': '…'}, ...}
    """
    t = Ticker(symbols, asynchronous=True, max_workers=workers, validate=True)
    prices = t.price or {}
    profiles = t.asset_profile or {}

    meta = {}
    for sym in symbols:
        code = sym.replace(".T", "")
        p = prices.get(sym)
        if isinstance(p, list) and p:
            p = p[0]
        if not isinstance(p, dict):
            p = {}

        prof = profiles.get(sym)
        if isinstance(prof, list) and prof:
            prof = prof[0]
        if not isinstance(prof, dict):
            prof = {}

        name = p.get("longName") or p.get("shortName") or ""
        sector = prof.get("sector") or ""
        industry = prof.get("industry") or ""
        meta[code] = {
            "name": name,
            "sector": sector or industry,
            "industry": industry,
        }
    return meta

# --- OHLCV 取得（並列） ---------------------------------------------------
def fetch_history(symbol, start, end):
    """
    yfinance で履歴取得。空なら None。
    """
    df = yf.download(symbol, start=start, end=end + dt.timedelta(days=1),
                     progress=False, auto_adjust=False, threads=False)
    if df is None or df.empty:
        return None
    df = df.rename(columns={"Close": "close", "Volume": "volume"}).reset_index()
    df["date"] = df["Date"].dt.strftime("%Y-%m-%d")
    return df[["date", "close", "volume"]]

class Command(BaseCommand):
    help = "日本株・全上場を外部から取得（名称/業種つき）し、OHLCVスナップショット生成（並列対応）"

    def add_arguments(self, p):
        p.add_argument("--asof", default=None)
        p.add_argument("--days", type=int, default=420)
        p.add_argument("--workers", type=int, default=12)
        p.add_argument("--min", dest="min_code", type=int, default=1300)
        p.add_argument("--max", dest="max_code", type=int, default=9999)
        p.add_argument("--limit", type=int, default=None)  # 検証用（本番は未指定）

    def handle(self, *args, **o):
        asof = o["asof"] or timezone.now().date().isoformat()
        end = dt.datetime.strptime(asof, "%Y-%m-%d")
        start = end - dt.timedelta(days=o["days"] * 2)  # 休日バッファ

        out_dir = Path(f"media/ohlcv/snapshots/{asof}")
        out_dir.mkdir(parents=True, exist_ok=True)
        out_csv = out_dir / "ohlcv.csv"

        self.stdout.write("[alljp] enumerate & validate symbols via yfinance …")
        symbols = enumerate_jp_symbols(o["min_code"], o["max_code"], o["workers"])
        if o["limit"]:
            symbols = symbols[:o["limit"]]
        self.stdout.write(f"[alljp] valid symbols: {len(symbols)}")

        self.stdout.write("[alljp] fetch meta (name/sector) …")
        meta = fetch_meta(symbols, workers=o["workers"])

        self.stdout.write(f"[alljp] fetch histories in parallel (workers={o['workers']}) …")
        rows = []
        with ThreadPoolExecutor(max_workers=o["workers"]) as ex:
            futs = {ex.submit(fetch_history, sym, start, end): sym for sym in symbols}
            for fut in as_completed(futs):
                sym = futs[fut]
                code = sym.replace(".T", "")
                try:
                    df = fut.result()
                    if df is None or df.empty:
                        continue
                    m = meta.get(code, {})
                    name = m.get("name", "")
                    sector = m.get("sector") or m.get("industry") or ""

                    df.insert(0, "code", code)
                    df["name"] = name
                    df["sector"] = sector
                    rows.append(df)
                except Exception as e:
                    self.stderr.write(f"[skip] {sym}: {e}")

        if not rows:
            raise CommandError("取得できた履歴が0件でした。")

        out = pd.concat(rows, ignore_index=True)
        out = out[["code", "date", "close", "volume", "name", "sector"]]
        out.to_csv(out_csv, index=False)
        self.stdout.write(self.style.SUCCESS(
            f"[alljp] done: codes={out['code'].nunique()} rows={len(out)} out={out_csv}"
        ))