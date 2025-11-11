from __future__ import annotations
import os, io, json, unicodedata, re
from typing import Optional, Tuple
import pandas as pd
import requests
from django.core.management.base import BaseCommand, CommandError

# JPX 公式の上場銘柄一覧（Excel）
DEFAULT_XLS_URL = "https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xls"

# プロジェクト直下の data/ に出力
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
DATA_DIR = os.path.join(BASE_DIR, "data")
CSV_PATH = os.path.join(DATA_DIR, "tse_list.csv")
JSON_PATH = os.path.join(DATA_DIR, "tse_list.json")


# ---------- 文字クレンジング ----------
def clean_text(s: Optional[str]) -> str:
    """
    ゼロ幅や私用領域、制御文字を除去して正規化
    """
    if s is None:
        return ""
    s = unicodedata.normalize("NFKC", str(s))
    s = re.sub(r"[\uE000-\uF8FF\u200B-\u200D\u2060\uFEFF]", "", s)  # 私用領域・ゼロ幅
    s = re.sub(r"[\x00-\x1F\x7F]", "", s)  # 制御文字
    return s.strip()


# 列名の候補（ゆるく拾う）
CODE_KEYS = {"code", "ｺｰﾄﾞ", "コード", "銘柄コード"}
NAME_KEYS = {"name", "銘柄名"}


def detect_code_name_columns(df: pd.DataFrame) -> Tuple[Optional[str], Optional[str]]:
    """
    シートごとに列名の表記ゆれがあるので、低レベル正規化して "code" / "name" を推定。
    """
    norm_map = {c: clean_text(c).lower() for c in df.columns}

    def pick(keys) -> Optional[str]:
        for raw, low in norm_map.items():
            for k in keys:
                if k in low:
                    return raw
        return None

    return pick(CODE_KEYS), pick(NAME_KEYS)


class Command(BaseCommand):
    help = "JPXの上場銘柄一覧を取得し、code・nameのみを data/tse_list.(csv|json) に保存"

    def add_arguments(self, parser):
        parser.add_argument("--url", type=str, default=DEFAULT_XLS_URL,
                            help="ExcelファイルのURL（既定: JPX公式 data_j.xls）")
        parser.add_argument("--timeout", type=int, default=60,
                            help="ダウンロードのタイムアウト秒（既定: 60）")

    def handle(self, *args, **opts):
        url = opts.get("url") or DEFAULT_XLS_URL
        timeout = int(opts.get("timeout") or 60)
        self.stdout.write(f"Downloading: {url}")
        os.makedirs(DATA_DIR, exist_ok=True)

        # --- ダウンロード ---
        try:
            resp = requests.get(url, timeout=timeout)
            resp.raise_for_status()
        except Exception as e:
            raise CommandError(f"ダウンロードに失敗: {e}")

        # --- Excel を読み込み、全シートから code/name を抽出 ---
        xls = pd.ExcelFile(io.BytesIO(resp.content))
        frames = []
        for sheet in xls.sheet_names:
            try:
                df = xls.parse(sheet_name=sheet, dtype=str)
                code_col, name_col = detect_code_name_columns(df)
                if not code_col or not name_col:
                    continue

                sub = df[[code_col, name_col]].copy()
                sub.rename(columns={code_col: "code", name_col: "name"}, inplace=True)

                # クリーニング
                sub["code"] = sub["code"].map(clean_text)
                sub["name"] = sub["name"].map(clean_text)

                # コードは 4〜5桁の数字のみ
                sub = sub[sub["code"].str.fullmatch(r"\d{4,5}")]
                frames.append(sub)
            except Exception:
                # シートによって崩れていてもスキップ
                continue

        if not frames:
            raise CommandError("code/name を含む対象シートが見つかりません。")

        # --- マージ＆重複除去（後勝ち） ---
        df = pd.concat(frames, ignore_index=True)
        df = df.drop_duplicates(subset=["code"], keep="last")

        # 最終整形（code・name のみ）
        df_out = df[["code", "name"]].copy()

        # --- 保存（CSV / JSON）---
        df_out.to_csv(CSV_PATH, index=False, encoding="utf-8-sig")
        with open(JSON_PATH, "w", encoding="utf-8") as f:
            json.dump(
                {r["code"]: {"name": r["name"]} for _, r in df_out.iterrows()},
                f,
                ensure_ascii=False,
                indent=2,
            )

        self.stdout.write(self.style.SUCCESS(f"Saved CSV : {CSV_PATH}"))
        self.stdout.write(self.style.SUCCESS(f"Saved JSON: {JSON_PATH}"))
        self.stdout.write(self.style.SUCCESS(f"Done. records={len(df_out)}"))