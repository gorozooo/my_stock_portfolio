from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import pandas as pd
import datetime as dt
import yfinance as yf
import time, re

def clean_txt(s):
    if pd.isna(s): return ""
    s = str(s)
    s = re.sub(r"[\u200B-\u200D\uFEFF\u2060\u00AD]", "", s)      # ゼロ幅
    s = re.sub(r"[\uE000-\uF8FF]", "", s)                         # 私用領域
    s = re.sub(r"[\x00-\x1F\x7F-\x9F]", "", s)                    # 制御文字
    return s.strip()

def fetch_history(code: str, start: dt.datetime, end: dt.datetime, retries=2, pause=1.5):
    sym = f"{code}.T"
    for i in range(retries + 1):
        try:
            df = yf.download(sym, start=start, end=end + dt.timedelta(days=1),
                             progress=False, auto_adjust=False, threads=False)
            if df is not None and not df.empty:
                df = df.rename(columns={"Close":"close","Volume":"volume"}).reset_index()
                df["date"] = df["Date"].dt.strftime("%Y-%m-%d")
                return df[["date","close","volume"]]
        except Exception:
            pass
        time.sleep(pause*(2**i))
    return None

class Command(BaseCommand):
    help = "JPXのmaster CSV（code,name,sector）を元に、Yahooから日足を取って ohlcv.csv を作る"

    def add_arguments(self, p):
        p.add_argument("--master", required=True, help="JPX master CSV（code,name,sector）")
        p.add_argument("--asof", default=None)
        p.add_argument("--days", type=int, default=400)
        p.add_argument("--limit", type=int, default=None)   # テスト用
        p.add_argument("--workers", type=int, default=3)    # 低並列で安定

    def handle(self, *args, **o):
        asof = o["asof"] or timezone.now().date().isoformat()
        end = dt.datetime.strptime(asof, "%Y-%m-%d")
        start = end - dt.timedelta(days=o["days"])

        # master 読み込み（厳密に dtype=str）
        mpath = Path(o["master"])
        if not mpath.exists():
            raise CommandError(f"master CSV が見つかりません: {mpath}")
        mdf = pd.read_csv(mpath, dtype=str)
        mdf["code"] = mdf["code"].astype(str).str.extract(r"(\d{4})")[0]
        mdf["name"] = mdf["name"].map(clean_txt)
        mdf["sector"] = mdf["sector"].map(lambda s: clean_txt(s).replace(".0",""))
        mdf = mdf.dropna(subset=["code"]).drop_duplicates(subset=["code"])

        if o["limit"]:
            mdf = mdf.head(int(o["limit"]))

        codes = mdf["code"].tolist()
        meta = {r.code: (r.name, r.sector) for r in mdf.itertuples(index=False)}

        out_dir = Path(f"media/ohlcv/snapshots/{asof}")
        out_dir.mkdir(parents=True, exist_ok=True)
        out_csv = out_dir / "ohlcv.csv"

        rows = []
        def task(code):
            df = fetch_history(code, start, end)
            if df is None or df.empty:
                return None
            name, sector = meta.get(code, ("",""))
            df.insert(0, "code", code)
            df["name"] = name
            df["sector"] = sector
            return df

        with ThreadPoolExecutor(max_workers=o["workers"]) as ex:
            futs = {ex.submit(task, c): c for c in codes}
            for fut in as_completed(futs):
                res = fut.result()
                if res is not None:
                    rows.append(res)

        if not rows:
            raise CommandError("取得できた履歴が0件でした（429の可能性）。")

        out = pd.concat(rows, ignore_index=True)
        out = out[["code","date","close","volume","name","sector"]]
        # 保存前に最終正規化（列固定・ゼロ幅除去・数値健全化）
        out["code"] = out["code"].astype(str).str.extract(r"(\d{4})")[0]
        out["name"] = out["name"].map(clean_txt)
        out["sector"] = out["sector"].map(clean_txt)
        out["close"] = pd.to_numeric(out["close"], errors="coerce")
        out["volume"] = pd.to_numeric(out["volume"], errors="coerce")
        out = out.dropna(subset=["code","date","close","volume"]).sort_values(["code","date"])
        out.to_csv(out_csv, index=False, encoding="utf-8", line_terminator="\n")
        self.stdout.write(self.style.SUCCESS(
            f"[SNAPSHOT] codes={out['code'].nunique()} rows={len(out)} -> {out_csv}"
        ))