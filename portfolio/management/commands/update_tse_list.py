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

# ---------- 文字クレンジングを強化 ----------
_ZW = r"\u200B\u200C\u200D\u2060\uFEFF"
_PUA = r"\uE000-\uF8FF"
_CTRL = r"\x00-\x1F\x7F"

_ZW_RE  = re.compile(f"[{_ZW}]")
_PUA_RE = re.compile(f"[{_PUA}]")
_CTRL_RE= re.compile(f"[{_CTRL}]")

def clean_text(s: Optional[str]) -> str:
    if s is None:
        return ""
    s = unicodedata.normalize("NFKC", str(s))
    # 制御・ゼロ幅・私用領域を除去（見えない“”対策）
    s = _CTRL_RE.sub("", s)
    s = _ZW_RE.sub("", s)
    s = _PUA_RE.sub("", s)
    return s.strip()

# 列名候補（小文字）
CODE_KEYS   = {"code","ｺｰﾄﾞ","コード","こーど","銘柄コード","証券コード"}
NAME_KEYS   = {"name","銘柄名","めいがらめい"}
SECTOR_KEYS = {"業種","業種名","33業種","業種分類","せくたー","sector"}
MARKET_KEYS = {"市場","市場区分","市場・商品区分","市場・商品","market","上場市場"}

def pick_col_exact(norm_map: Dict[str,str], candidates: set) -> Optional[str]:
    for raw, low in norm_map.items():
        if low in candidates:
            return raw
    return None

def pick_col_fuzzy(norm_map: Dict[str,str], substrings: set) -> Optional[str]:
    # “業種(33業種)” “市場・商品区分（内国株式）” などを拾う
    for raw, low in norm_map.items():
        for sub in substrings:
            if sub in low:
                return raw
    return None

def detect_columns(df: pd.DataFrame) -> Tuple[Optional[str],Optional[str],Optional[str],Optional[str]]:
    norm_map = {c: clean_text(c).lower() for c in df.columns}

    code   = pick_col_exact(norm_map, CODE_KEYS)   or pick_col_fuzzy(norm_map, CODE_KEYS)
    name   = pick_col_exact(norm_map, NAME_KEYS)   or pick_col_fuzzy(norm_map, NAME_KEYS)
    sector = pick_col_exact(norm_map, SECTOR_KEYS) or pick_col_fuzzy(norm_map, SECTOR_KEYS)
    market = pick_col_exact(norm_map, MARKET_KEYS) or pick_col_fuzzy(norm_map, MARKET_KEYS)
    return code, name, sector, market

# 33業種のゆらぎ補正（最低限）
SECTOR_NORMALIZE = {
    "情報・通信": "情報・通信業",
    "電気機器": "電気機器",
    "機械": "機械",
    "医薬品": "医薬品",
    "銀行": "銀行業",
    "輸送用機器": "輸送用機器",
    "小売": "小売業",
    "卸売": "卸売業",
    "化学": "化学",
    "金属製品": "金属製品",
    "非鉄金属": "非鉄金属",
    "鉄鋼": "鉄鋼",
    "建設": "建設業",
    "不動産": "不動産業",
    "サービス": "サービス業",
    "食料品": "食料品",
    "水産・農林": "水産・農林業",
    "鉱業": "鉱業",
    "石油・石炭": "石油・石炭製品",
    "ゴム製品": "ゴム製品",
    "ガラス・土石": "ガラス・土石製品",
    "繊維製品": "繊維製品",
    "紙・パルプ": "パルプ・紙",
    "医療精密": "精密機器",
    "その他金融": "その他金融業",
    "保険": "保険業",
    "証券・商品先物": "証券、商品先物取引業",
    "陸運": "陸運業",
    "海運": "海運業",
    "空運": "空運業",
    "倉庫・運輸関連": "倉庫・運輸関連業",
    "電気・ガス": "電気・ガス業",
}

def normalize_sector(s: str) -> str:
    s = clean_text(s)
    return SECTOR_NORMALIZE.get(s, s)

class Command(BaseCommand):
    help = "JPXの上場銘柄一覧を取得し、code,name,sector,market を data/tse_list.(csv|json) に保存"

    def add_arguments(self, parser):
        parser.add_argument("--url", help="ExcelのURL（省略可・既定URL使用）")

    def handle(self, *args, **opts):
        url = opts.get("url") or os.environ.get("TSE_XLS_URL") or DEFAULT_XLS_URL
        self.stdout.write(f"Downloading: {url}")
        os.makedirs(DATA_DIR, exist_ok=True)

        try:
            resp = requests.get(url, timeout=60)
            resp.raise_for_status()
        except Exception as e:
            raise CommandError(f"ダウンロードに失敗: {e}")

        self.stdout.write("Reading Excel sheets...")
        try:
            xls = pd.ExcelFile(io.BytesIO(resp.content))
        except Exception as e:
            raise CommandError(f"Excel解析に失敗: {e}")

        frames: List[pd.DataFrame] = []
        for sheet in xls.sheet_names:
            try:
                df = xls.parse(sheet_name=sheet, dtype=str, header=0)
                code_col, name_col, sector_col, market_col = detect_columns(df)
                if code_col and name_col:
                    use_cols = [code_col, name_col]
                    if sector_col: use_cols.append(sector_col)
                    if market_col: use_cols.append(market_col)
                    sub = df[use_cols].copy()
                    rename = {code_col:"code", name_col:"name"}
                    if sector_col: rename[sector_col] = "sector"
                    if market_col: rename[market_col] = "market"
                    sub.rename(columns=rename, inplace=True)
                    frames.append(sub)
            except Exception:
                continue

        if not frames:
            raise CommandError("コード/銘柄名の列を持つシートが見つかりませんでした。")

        df = pd.concat(frames, ignore_index=True)

        # クレンジング
        for col in ["code","name","sector","market"]:
            if col in df.columns:
                df[col] = df[col].map(clean_text)

        # コードは4〜5桁のみ、重複は最後優先
        df = df[df["code"].str.fullmatch(r"\d{4,5}")]
        df = df.drop_duplicates(subset=["code"], keep="last").sort_values("code")

        # 業種正規化（列がある場合のみ）
        if "sector" in df.columns:
            df["sector"] = df["sector"].map(lambda s: normalize_sector(s) if s else "")

        # 保存（CSV）
        df_out = df.copy()
        if "sector" not in df_out.columns: df_out["sector"] = ""
        if "market" not in df_out.columns: df_out["market"] = ""
        df_out = df_out[["code","name","sector","market"]]
        df_out.to_csv(CSV_PATH, index=False, encoding="utf-8-sig")

        # 保存（JSON）
        payload = {
            row["code"]: {
                "name": row["name"],
                "sector": row["sector"],
                "market": row["market"],
            }
            for _, row in df_out.iterrows()
        }
        with open(JSON_PATH, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

        self.stdout.write(self.style.SUCCESS(f"Saved CSV:  {CSV_PATH} ({len(df_out)} rows)"))
        self.stdout.write(self.style.SUCCESS(f"Saved JSON: {JSON_PATH}"))
        self.stdout.write(self.style.SUCCESS("Done."))