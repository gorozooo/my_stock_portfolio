from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from pathlib import Path
import pandas as pd
import datetime as dt
import sys

# 依存: pip install yfinance pandas
try:
    import yfinance as yf
except Exception as e:
    raise CommandError(
        "yfinance が見つかりません。先に `pip install yfinance pandas` を実行してください。"
    )

class Command(BaseCommand):
    help = "コードリストから OHLCV を一括取得し、media/ohlcv/snapshots/<asof>/ohlcv.csv を生成します。"

    def add_arguments(self, parser):
        parser.add_argument("--codes", required=True,
                            help="コードリスト。ファイルパス（1行1コード）またはカンマ区切り文字列。例: data/universe/jpx_top50.txt")
        parser.add_argument("--asof", default=None, help="日付 YYYY-MM-DD（省略時は今日）")
        parser.add_argument("--days", type=int, default=420, help="取得日数（営業日ベースで約1.5年想定）")

    def handle(self, *args, **opts):
        asof = opts["asof"] or timezone.now().date().isoformat()
        days = opts["days"]

        # 出力パス
        out_dir = Path(f"media/ohlcv/snapshots/{asof}")
        out_dir.mkdir(parents=True, exist_ok=True)
        out_csv = out_dir / "ohlcv.csv"

        # コードリストの解釈
        raw = opts["codes"].strip()
        p = Path(raw)
        if p.exists():
            codes = [line.strip() for line in p.read_text(encoding="utf-8", errors="ignore").splitlines() if line.strip()]
        else:
            codes = [c.strip() for c in raw.replace(",", "\n").splitlines() if c.strip()]

        if not codes:
            raise CommandError("--codes にコードがありません。ファイルパスかカンマ/改行区切りで渡してください。")

        # 期間
        end = dt.datetime.strptime(asof, "%Y-%m-%d")
        start = end - dt.timedelta(days=days*2)  # 休日分のバッファ

        # 出力CSVのヘッダ
        with out_csv.open("w", encoding="utf-8") as f:
            f.write("code,date,close,volume,name,sector\n")

        ok = 0
        for i, code in enumerate(codes, 1):
            code = code.strip()
            if not code or code.lower() == "nan" or code.lower().startswith("code"):
                continue

            # TSE の Yahoo は "XXXX.T" で取れることが多い
            ycode = code
            if not ycode.endswith(".T"):
                ycode = f"{code}.T"

            self.stdout.write(f"[{i}/{len(codes)}] fetch {ycode}")

            try:
                df = yf.download(ycode, start=start, end=end + dt.timedelta(days=1), progress=False, auto_adjust=False)
                if df is None or df.empty:
                    self.stderr.write(f"  -> no data: {ycode}")
                    continue

                # 必要な列に整形
                df = df.rename(columns={"Close": "close", "Volume": "volume"})
                df = df.reset_index()
                df["code"] = code
                df["date"] = df["Date"].dt.strftime("%Y-%m-%d")
                df["name"] = ""     # 後でマスタ結合するならここで埋めてもOK
                df["sector"] = ""   # 同上
                out = df[["code", "date", "close", "volume", "name", "sector"]]

                # 追記
                out.to_csv(out_csv, mode="a", index=False, header=False)
                ok += 1

            except Exception as e:
                self.stderr.write(f"  -> fail {ycode}: {e}")

        self.stdout.write(self.style.SUCCESS(f"完了: {ok}銘柄 out={out_csv}"))