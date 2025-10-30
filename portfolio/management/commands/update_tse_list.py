# portfolio/management/commands/update_tse_list.py
from __future__ import annotations
import os, io, json, unicodedata, re
from typing import Optional, Tuple, Dict, List
import pandas as pd
import requests
from django.core.management.base import BaseCommand, CommandError

DEFAULT_XLS_URL = "https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xls"

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
DATA_DIR = os.path.join(BASE_DIR, "data")
CSV_PATH = os.path.join(DATA_DIR, "tse_list.csv")
JSON_PATH = os.path.join(DATA_DIR, "tse_list.json")

# ---------- 文字クレンジング ----------
def clean_text(s: Optional[str]) -> str:
    if s is None:
        return ""
    s = unicodedata.normalize("NFKC", str(s))
    s = re.sub(r"[\uE000-\uF8FF\u200B-\u200D\u2060\uFEFF]", "", s)  # 私用領域・ゼロ幅
    s = re.sub(r"[\x00-\x1F\x7F]", "", s)  # 制御文字
    return s.strip()

CODE_KEYS   = {"code","ｺｰﾄﾞ","コード","銘柄コード"}
NAME_KEYS   = {"name","銘柄名"}
SECTOR_KEYS = {"業種","業種名","33業種","業種分類","sector"}
MARKET_KEYS = {"市場","市場区分","市場・商品区分","market"}

def detect_columns(df: pd.DataFrame):
    norm_map = {c: clean_text(c).lower() for c in df.columns}
    def pick(keys): 
        for raw, low in norm_map.items():
            for k in keys:
                if k in low: return raw
    return pick(CODE_KEYS), pick(NAME_KEYS), pick(SECTOR_KEYS), pick(MARKET_KEYS)

# ---------- 33業種コードマップ ----------
SECTOR_CODE_MAP = {
    "50": "水産・農林業", "1050": "食料品", "2050": "建設業", "3050": "繊維製品",
    "3100": "パルプ・紙", "3150": "化学", "3200": "医薬品", "3250": "石油・石炭製品",
    "3300": "ゴム製品", "3350": "ガラス・土石製品", "3400": "鉄鋼", "3450": "非鉄金属",
    "3500": "金属製品", "3550": "機械", "3600": "電気機器", "3650": "輸送用機器",
    "3700": "精密機器", "3750": "その他製品", "5050": "電気・ガス業", "6050": "陸運業",
    "6100": "海運業", "6150": "空運業", "6200": "倉庫・運輸関連業", "7050": "情報・通信業",
    "8050": "卸売業", "8100": "小売業", "9050": "銀行業", "9100": "証券、商品先物取引業",
    "9150": "保険業", "9200": "その他金融業", "10050": "不動産業", "10500": "サービス業",
}

def normalize_sector(s: str) -> str:
    s = clean_text(s)
    # コードであればマッピング
    if s.isdigit():
        return SECTOR_CODE_MAP.get(s, "")
    # 一般日本語マッチ
    for v in SECTOR_CODE_MAP.values():
        if s in v or v in s:
            return v
    return s

class Command(BaseCommand):
    help = "JPXの上場銘柄一覧を取得し、code,name,sector,market を data/tse_list.(csv|json) に保存"

    def handle(self, *args, **opts):
        url = opts.get("url") or DEFAULT_XLS_URL
        self.stdout.write(f"Downloading: {url}")
        os.makedirs(DATA_DIR, exist_ok=True)

        try:
            resp = requests.get(url, timeout=60)
            resp.raise_for_status()
        except Exception as e:
            raise CommandError(f"ダウンロードに失敗: {e}")

        xls = pd.ExcelFile(io.BytesIO(resp.content))
        frames = []
        for sheet in xls.sheet_names:
            try:
                df = xls.parse(sheet_name=sheet, dtype=str)
                code_col, name_col, sector_col, market_col = detect_columns(df)
                if code_col and name_col:
                    cols = [code_col, name_col]
                    if sector_col: cols.append(sector_col)
                    if market_col: cols.append(market_col)
                    sub = df[cols].copy()
                    sub.rename(columns={
                        code_col:"code", name_col:"name",
                        sector_col:"sector" if sector_col else None,
                        market_col:"market" if market_col else None
                    }, inplace=True)
                    frames.append(sub)
            except Exception:
                continue

        if not frames:
            raise CommandError("対象シートが見つかりません。")

        df = pd.concat(frames, ignore_index=True)
        for c in ["code","name","sector","market"]:
            if c in df.columns:
                df[c] = df[c].map(clean_text)

        df = df[df["code"].str.fullmatch(r"\d{4,5}")]
        df = df.drop_duplicates(subset=["code"], keep="last")

        if "sector" in df.columns:
            df["sector"] = df["sector"].map(normalize_sector)

        df_out = df.assign(
            sector=df.get("sector", ""),
            market=df.get("market", "")
        )[["code","name","sector","market"]]

        df_out.to_csv(CSV_PATH, index=False, encoding="utf-8-sig")
        json.dump(
            {r["code"]: dict(name=r["name"], sector=r["sector"], market=r["market"])
             for _, r in df_out.iterrows()},
            open(JSON_PATH,"w",encoding="utf-8"),
            ensure_ascii=False, indent=2
        )

        self.stdout.write(self.style.SUCCESS(f"Saved JSON: {JSON_PATH}"))
        self.stdout.write(self.style.SUCCESS("Done."))