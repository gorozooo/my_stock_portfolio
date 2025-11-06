from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import pandas as pd
import datetime as dt
import yfinance as yf
import time, re

def clean_txt(s):
    if s is None or (isinstance(s, float) and pd.isna(s)):
        return ""
    s = str(s)
    s = re.sub(r"[\u200B-\u200D\uFEFF\u2060\u00AD]", "", s)  # ゼロ幅
    s = re.sub(r"[\uE000-\uF8FF]", "", s)                     # 私用領域
    s = re.sub(r"[\x00-\x1F\x7F-\x9F]", "", s)                # 制御文字
    return s.strip()

def fetch_history(code: str, start: dt.datetime, end: dt.datetime, retries=2, pause=1.5):
    """Yahoo(日足)取得。軽いリトライ＆指数バックオフ。常に date/close/volume の3列で返す。"""
    sym = f"{code}.T"
    for i in range(retries + 1):
        try:
            df = yf.download(
                sym, start=start, end=end + dt.timedelta(days=1),
                progress=False, auto_adjust=False, threads=False,
            )
            if df is not None and not df.empty:
                df = df.rename(columns={"Close": "close", "Volume": "volume"}).reset_index()
                df["date"] = df["Date"].dt.strftime("%Y-%m-%d")
                return df[["date", "close", "volume"]]  # ← ここで3列に固定
        except Exception:
            pass
        time.sleep(pause * (2 ** i))
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

        # master の読み込み（厳密に文字列）
        mpath = Path(o["master"])
        if not mpath.exists():
            raise CommandError(f"master CSV が見つかりません: {mpath}")
        mdf = pd.read_csv(mpath, dtype=str, low_memory=False)
        for col in ["code", "name", "sector"]:
            if col not in mdf.columns:
                raise CommandError(f"master CSV に列がありません: {col}")

        mdf["code"]   = mdf["code"].astype(str).str.extract(r"(\d{4})")[0]
        mdf["name"]   = mdf["name"].map(clean_txt)
        mdf["sector"] = mdf["sector"].map(lambda s: clean_txt(s).replace(".0", ""))
        mdf = mdf.dropna(subset=["code"]).drop_duplicates(subset=["code"])

        if o["limit"]:
            mdf = mdf.head(int(o["limit"]))

        codes = mdf["code"].tolist()
        meta  = {r.code: (r.name, r.sector) for r in mdf.itertuples(index=False)}

        out_dir = Path(f"media/ohlcv/snapshots/{asof}")
        out_dir.mkdir(parents=True, exist_ok=True)
        out_csv = out_dir / "ohlcv.csv"

        rows = []

        def task(code):
            """ここで“6列だけ”のきれいなDFを作って返す（列重複を物理的に不可能にする）"""
            h = fetch_history(code, start, end)
            if h is None or h.empty:
                return None
            name, sector = meta.get(code, ("", ""))
            # 6列を新規DataFrameとして“明示的に構築”
            df = pd.DataFrame({
                "code":   [code] * len(h),
                "date":   h["date"].astype(str),
                "close":  pd.to_numeric(h["close"],  errors="coerce"),
                "volume": pd.to_numeric(h["volume"], errors="coerce"),
                "name":   [name]   * len(h),
                "sector": [sector] * len(h),
            })
            df = df.dropna(subset=["close","volume"])
            return df

        with ThreadPoolExecutor(max_workers=o["workers"]) as ex:
            futs = {ex.submit(task, c): c for c in codes}
            for fut in as_completed(futs):
                res = fut.result()
                if isinstance(res, pd.DataFrame) and not res.empty:
                    rows.append(res)

        if not rows:
            raise CommandError("取得結果に有効なDataFrameがありません。（429等の可能性。--workers/--limit を調整）")

        # ここまで来たら rows 内は「列固定のDF」だけ。結合→最終整形。
        df = pd.concat(rows, ignore_index=True)
        df = (
            df.dropna(subset=["code", "date", "close", "volume"])
              .sort_values(["code", "date"])
              .drop_duplicates()
        )

        # 6列固定で保存（重複カラムの心配はもうない）
        df.to_csv(out_csv, index=False, encoding="utf-8", line_terminator="\n")
        self.stdout.write(self.style.SUCCESS(
            f"[SNAPSHOT] codes={df['code'].nunique()} rows={len(df)} -> {out_csv}"
        ))