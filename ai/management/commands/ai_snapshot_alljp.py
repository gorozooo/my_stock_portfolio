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
    # ゼロ幅/私用領域/制御文字を除去
    s = re.sub(r"[\u200B-\u200D\uFEFF\u2060\u00AD]", "", s)
    s = re.sub(r"[\uE000-\uF8FF]", "", s)
    s = re.sub(r"[\x00-\x1F\x7F-\x9F]", "", s)
    return s.strip()

def fetch_history(code: str, start: dt.datetime, end: dt.datetime, retries=2, pause=1.5):
    """Yahoo(日足)取得。軽いリトライ付き。"""
    sym = f"{code}.T"
    for i in range(retries + 1):
        try:
            df = yf.download(
                sym,
                start=start,
                end=end + dt.timedelta(days=1),
                progress=False,
                auto_adjust=False,
                threads=False,
            )
            if df is not None and not df.empty:
                df = df.rename(columns={"Close": "close", "Volume": "volume"}).reset_index()
                df["date"] = df["Date"].dt.strftime("%Y-%m-%d")
                return df[["date", "close", "volume"]]
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

        # ---- master 読み込み（厳密に文字列）----
        mpath = Path(o["master"])
        if not mpath.exists():
            raise CommandError(f"master CSV が見つかりません: {mpath}")
        mdf = pd.read_csv(mpath, dtype=str, low_memory=False)
        # 必須列チェック
        for col in ["code", "name", "sector"]:
            if col not in mdf.columns:
                raise CommandError(f"master CSV に列がありません: {col}")

        # 正規化
        mdf["code"] = mdf["code"].astype(str).str.extract(r"(\d{4})")[0]
        mdf["name"] = mdf["name"].map(clean_txt)
        mdf["sector"] = mdf["sector"].map(lambda s: clean_txt(s).replace(".0", ""))
        mdf = mdf.dropna(subset=["code"]).drop_duplicates(subset=["code"])

        if o["limit"]:
            mdf = mdf.head(int(o["limit"]))

        codes = mdf["code"].tolist()
        meta = {r.code: (r.name, r.sector) for r in mdf.itertuples(index=False)}

        out_dir = Path(f"media/ohlcv/snapshots/{asof}")
        out_dir.mkdir(parents=True, exist_ok=True)
        out_csv = out_dir / "ohlcv.csv"

        # ---- 価格取得 ----
        rows = []

        def task(code):
            h = fetch_history(code, start, end)
            if h is None or h.empty:
                return None
            name, sector = meta.get(code, ("", ""))
            h.insert(0, "code", code)
            h["name"] = name
            h["sector"] = sector
            return h

        with ThreadPoolExecutor(max_workers=o["workers"]) as ex:
            futs = {ex.submit(task, c): c for c in codes}
            for fut in as_completed(futs):
                res = fut.result()
                if res is not None:
                    rows.append(res)

        if not rows:
            raise CommandError("取得できた履歴が0件でした（429等の可能性）。")

        # ---- DataFrame 結合＆最終正規化 ----
        df = pd.concat(rows, ignore_index=True)

        # 欲しい6列だけ固定。欠けてたら作る（安全側）
        want = ["code", "date", "close", "volume", "name", "sector"]
        for k in want:
            if k not in df.columns:
                df[k] = ""  # 足りない場合は空で補完
        df = df[want]

        # 型整形
        df["code"] = df["code"].astype(str).str.extract(r"(\d{4})")[0]
        df["name"] = df["name"].map(clean_txt)
        df["sector"] = df["sector"].map(clean_txt)
        df["close"] = pd.to_numeric(df["close"], errors="coerce")
        df["volume"] = pd.to_numeric(df["volume"], errors="coerce")

        df = df.dropna(subset=["code", "date", "close", "volume"]).sort_values(["code", "date"]).drop_duplicates()

        # 保存（UTF-8・改行統一）
        df.to_csv(out_csv, index=False, encoding="utf-8", line_terminator="\n")
        self.stdout.write(self.style.SUCCESS(
            f"[SNAPSHOT] codes={df['code'].nunique()} rows={len(df)} -> {out_csv}"
        ))