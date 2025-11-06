from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import pandas as pd
import datetime as dt
import yfinance as yf
import time
import re

def clean_txt(s):
    """ゼロ幅・私用領域・制御文字を除去してトリム"""
    if s is None or (isinstance(s, float) and pd.isna(s)):
        return ""
    s = str(s)
    s = re.sub(r"[\u200B-\u200D\uFEFF\u2060\u00AD]", "", s)  # ゼロ幅類
    s = re.sub(r"[\uE000-\uF8FF]", "", s)                     # 私用領域
    s = re.sub(r"[\x00-\x1F\x7F-\x9F]", "", s)                # 制御文字
    return s.strip()

def fetch_history(code: str, start: dt.datetime, end: dt.datetime, retries=2, pause=1.5):
    """Yahoo(日足)取得。軽いリトライ＆指数バックオフ付き。"""
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
                # DataFrame 以外（None/空/想定外型）は捨てる
                if isinstance(res, pd.DataFrame) and not res.empty:
                    rows.append(res)

        if not rows:
            raise CommandError("取得結果に有効なDataFrameがありません。（429等の可能性。--workers/--limit を調整）")

        # ---- DataFrame 結合 ----
        df = pd.concat(rows, ignore_index=True)

        # ---- カラム名の正規化＆重複排除 ----
        # 余計な列名ゆらぎを吸収
        rename_map = {}
        for c in df.columns:
            lc = str(c).strip().lower()
            if lc == "code": rename_map[c] = "code"
            elif lc == "date": rename_map[c] = "date"
            elif lc == "close": rename_map[c] = "close"
            elif lc == "volume": rename_map[c] = "volume"
            elif lc == "name": rename_map[c] = "name"
            elif "sector" in lc or "業種" in lc: rename_map[c] = "sector"
        df = df.rename(columns=rename_map)

        # 欲しい6列だけを抽出（足りなければ空で補完）
        want = ["code", "date", "close", "volume", "name", "sector"]
        for k in want:
            if k not in df.columns:
                df[k] = ""

        # 同名カラムが複数ある場合は先勝ちにする（重複カラム除去）
        df = df[want]
        df = df.loc[:, ~df.columns.duplicated(keep="first")]

        # もしなお "close" や "volume" が DataFrame なら先頭列を採用（保険）
        for k in ["close", "volume", "date", "code", "name", "sector"]:
            col = df[k]
            if isinstance(col, pd.DataFrame):
                df[k] = col.iloc[:, 0]

        # ---- 型整形・最終正規化 ----
        df["code"] = df["code"].astype(str).str.extract(r"(\d{4})")[0]
        df["name"] = df["name"].map(clean_txt)
        df["sector"] = df["sector"].map(clean_txt)

        # to_numeric は Series 前提。念のため Series 化してから変換。
        df["close"] = pd.Series(df["close"])
        df["volume"] = pd.Series(df["volume"])
        df["close"] = pd.to_numeric(df["close"], errors="coerce")
        df["volume"] = pd.to_numeric(df["volume"], errors="coerce")

        df = (
            df.dropna(subset=["code", "date", "close", "volume"])
              .sort_values(["code", "date"])
              .drop_duplicates()
        )

        # 保存（UTF-8・改行統一）
        df.to_csv(out_csv, index=False, encoding="utf-8", line_terminator="\n")
        self.stdout.write(self.style.SUCCESS(
            f"[SNAPSHOT] codes={df['code'].nunique()} rows={len(df)} -> {out_csv}"
        ))