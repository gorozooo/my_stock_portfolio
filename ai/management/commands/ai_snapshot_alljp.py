from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import pandas as pd
import datetime as dt
import yfinance as yf
import time
import re

# 33業種マップ（JPX: 50..83）
SECT33 = {
    50: '水産・農林業', 51: '鉱業', 52: '建設業', 53: '食料品', 54: '繊維製品', 55: 'パルプ・紙',
    56: '化学', 57: '医薬品', 58: '石油・石炭製品', 59: 'ゴム製品', 60: 'ガラス・土石製品',
    61: '鉄鋼', 62: '非鉄金属', 63: '金属製品', 64: '機械', 65: '電気機器', 66: '輸送用機器',
    67: '精密機器', 68: 'その他製品', 69: '電気・ガス業', 70: '陸運業', 71: '海運業', 72: '空運業',
    73: '倉庫・運輸関連業', 74: '情報・通信業', 75: '卸売業', 76: '小売業', 77: '銀行業',
    78: '証券、商品先物取引業', 79: '保険業', 80: 'その他金融業', 81: '不動産業', 82: 'サービス業', 83: 'その他'
}

def clean_txt(s):
    """ゼロ幅・私用領域・制御文字を除去してトリム"""
    if s is None or (isinstance(s, float) and pd.isna(s)):
        return ""
    s = str(s)
    s = re.sub(r"[\u200B-\u200D\uFEFF\u2060\u00AD]", "", s)  # ゼロ幅類
    s = re.sub(r"[\uE000-\uF8FF]", "", s)                     # 私用領域
    s = re.sub(r"[\x00-\x1F\x7F-\x9F]", "", s)                # 制御文字
    return s.strip()

def normalize_sector(s: str) -> str:
    """
    JPXマスターの sector 列を日本語の33業種名へ正規化。
    - "50" / "50.0" / 50 → "水産・農林業"
    - 既に日本語ならそのまま
    - 空/不明は "-" を返す
    """
    txt = clean_txt(s)
    if not txt:
        return "-"
    m = re.fullmatch(r"(\d+)(?:\.0)?", txt)
    if m:
        code = int(m.group(1))
        return SECT33.get(code, "-")
    # 既に日本語・文字列ならそのまま（数字だけは却下）
    if re.fullmatch(r"\d+(?:\.\d+)?", txt):
        return "-"
    return txt

def fetch_history(code: str, start: dt.datetime, end: dt.datetime, retries=2, pause=1.5):
    """Yahoo(日足)取得。軽いリトライ＆指数バックオフ付き。必ず date/close/volume の3列を返す。"""
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
                # 3列に強制縮約（多重列/重複列の先頭を採用）
                date_col = df["Date"] if "Date" in df.columns else df.iloc[:, 0]
                close_col = df["close"] if "close" in df.columns else df.filter(regex=r"(?i)^close$").iloc[:, 0]
                if isinstance(close_col, pd.DataFrame):
                    close_col = close_col.iloc[:, 0]
                volume_col = df["volume"] if "volume" in df.columns else df.filter(regex=r"(?i)^volume$").iloc[:, 0]
                if isinstance(volume_col, pd.DataFrame):
                    volume_col = volume_col.iloc[:, 0]

                out = pd.DataFrame({
                    "date":   pd.to_datetime(date_col, errors="coerce").dt.strftime("%Y-%m-%d"),
                    "close":  close_col,
                    "volume": volume_col,
                })
                return out
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

        # クリーニング
        mdf["code"] = mdf["code"].astype(str).str.extract(r"(\d{4})")[0]
        mdf["name"] = mdf["name"].map(clean_txt)
        # sectorは必ず日本語へ正規化
        mdf["sector"] = mdf["sector"].map(normalize_sector)
        mdf = mdf.dropna(subset=["code"]).drop_duplicates(subset=["code"])

        if o["limit"]:
            mdf = mdf.head(int(o["limit"]))

        codes = mdf["code"].tolist()
        # meta: code -> (name, sector_jp)
        meta = {r.code: (r.name or "", r.sector or "-") for r in mdf.itertuples(index=False)}

        out_dir = Path(f"media/ohlcv/snapshots/{asof}")
        out_dir.mkdir(parents=True, exist_ok=True)
        out_csv = out_dir / "ohlcv.csv"

        rows = []

        def task(code):
            """ここで“6列だけ”のきれいなDFを作って返す（常に一次元Seriesに変換）"""
            h = fetch_history(code, start, end)
            if h is None or h.empty:
                return None

            # 1D 保証（close/volume が DataFrame の場合は先頭列のみ）
            if isinstance(h["close"], pd.DataFrame):
                h["close"] = h["close"].iloc[:, 0]
            if isinstance(h["volume"], pd.DataFrame):
                h["volume"] = h["volume"].iloc[:, 0]

            # 型整形（この段階で必ず Series/1D にする）
            h = h.copy()
            h["date"]   = pd.to_datetime(h["date"], errors="coerce").dt.strftime("%Y-%m-%d")
            h["close"]  = pd.to_numeric(pd.Series(h["close"]),  errors="coerce")
            h["volume"] = pd.to_numeric(pd.Series(h["volume"]), errors="coerce")
            h = h.dropna(subset=["date", "close", "volume"])
            if h.empty:
                return None

            name, sector_jp = meta.get(code, ("", "-"))
            # 6列を“明示的に構築”（列重複の余地をなくす）
            df_row = pd.DataFrame({
                "code":   [code]       * len(h),
                "date":   h["date"].astype(str),
                "close":  h["close"].astype(float),
                "volume": h["volume"].astype(float),
                "name":   [name]       * len(h),
                "sector": [sector_jp]  * len(h),   # ★ 日本語の33業種名のみを書き出す
            })
            return df_row

        with ThreadPoolExecutor(max_workers=o["workers"]) as ex:
            futs = {ex.submit(task, c): c for c in codes}
            for fut in as_completed(futs):
                res = fut.result()
                if isinstance(res, pd.DataFrame) and not res.empty:
                    rows.append(res)

        if not rows:
            raise CommandError("取得結果に有効なDataFrameがありません。（429等の可能性。--workers/--limit を調整）")

        # rows は既に6列固定。結合→並べ替え→重複除去だけ。
        df = pd.concat(rows, ignore_index=True)
        df = (
            df.dropna(subset=["code", "date", "close", "volume"])
              .sort_values(["code", "date"])
              .drop_duplicates()
        )

        # 6列固定で保存（sector は必ず日本語名）
        df.to_csv(out_csv, index=False, encoding="utf-8", lineterminator="\n")
        self.stdout.write(self.style.SUCCESS(
            f"[SNAPSHOT] codes={df['code'].nunique()} rows={len(df)} -> {out_csv}"
        ))