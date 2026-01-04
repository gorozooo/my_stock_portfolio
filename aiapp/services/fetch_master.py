# -*- coding: utf-8 -*-
from __future__ import annotations

import io
import os
import re
import unicodedata
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple

import pandas as pd
import requests
from django.db import transaction

from aiapp.models import StockMaster

# =========================================================
# JPX添付Excelを直接ダウンロードし、全シート走査で
# code / name / sector_code / sector_name を恒久的に更新。
# ・市場区分は扱わない
# ・旧フォーマット（1桁コード・旧業種名）も正規33業種に統一
# ・ETF/ETNは空業種時に付与
# =========================================================

DEFAULT_JPX_XLS_URL = os.getenv(
    "AIAPP_JPX_URL",
    "https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xls",
)

HTTP_TIMEOUT = float(os.getenv("AIAPP_HTTP_TIMEOUT", "60"))
HTTP_UA = os.getenv(
    "AIAPP_HTTP_UA",
    "Mozilla/5.0 (compatible; MyStockPortfolioBot/1.0)",
)

# ---------- 文字クレンジング ----------
DUMMY_TOKENS = {"-", "—", "－", "–", "―"}

def clean_text(s: Optional[str]) -> str:
    if s is None:
        return ""
    s = unicodedata.normalize("NFKC", str(s))
    s = re.sub(r"[\uE000-\uF8FF\u200B-\u200D\u2060\uFEFF]", "", s)
    s = re.sub(r"[\x00-\x1F\x7F]", "", s)
    s = s.strip()
    if s in DUMMY_TOKENS:
        return ""
    return s

# ---------- 列名検出 ----------
CODE_KEYS   = {"code", "ｺｰﾄﾞ", "コード", "銘柄コード"}
NAME_KEYS   = {"name", "銘柄名"}
SECTOR_KEYS = {"業種", "業種名", "33業種", "業種分類", "sector"}

def pick_code_col(df: pd.DataFrame) -> Optional[str]:
    low = {c: clean_text(c).lower() for c in df.columns}
    for raw, lowc in low.items():
        if any(k in lowc for k in CODE_KEYS):
            return raw
    return None

def pick_name_col(df: pd.DataFrame) -> Optional[str]:
    low = {c: clean_text(c).lower() for c in df.columns}
    for raw, lowc in low.items():
        if any(k in lowc for k in NAME_KEYS):
            return raw
    return None

def find_sector_candidates(df: pd.DataFrame) -> List[str]:
    low = {c: clean_text(c).lower() for c in df.columns}
    return [raw for raw, lowc in low.items() if any(k in lowc for k in SECTOR_KEYS)]

# ---------- 現行33業種 ----------
SECTOR_CODE_MAP: Dict[str, str] = {
    "50": "水産・農林業",
    "1050": "食料品",
    "2050": "建設業",
    "3050": "繊維製品",
    "3100": "パルプ・紙",
    "3150": "化学",
    "3200": "医薬品",
    "3250": "石油・石炭製品",
    "3300": "ゴム製品",
    "3350": "ガラス・土石製品",
    "3400": "鉄鋼",
    "3450": "非鉄金属",
    "3500": "金属製品",
    "3550": "機械",
    "3600": "電気機器",
    "3650": "輸送用機器",
    "3700": "精密機器",
    "3750": "その他製品",
    "5050": "電気・ガス業",
    "6050": "陸運業",
    "6100": "海運業",
    "6150": "空運業",
    "6200": "倉庫・運輸関連業",
    "7050": "情報・通信業",
    "8050": "卸売業",
    "8100": "小売業",
    "9050": "銀行業",
    "9100": "証券、商品先物取引業",
    "9150": "保険業",
    "9200": "その他金融業",
    "10050": "不動産業",
    "10500": "サービス業",
}
REV_SECTOR_MAP: Dict[str, str] = {v: k for k, v in SECTOR_CODE_MAP.items()}
CANON_SECTORS: List[str] = list(SECTOR_CODE_MAP.values())

# ---------- 旧フォーマット補正 ----------
LEGACY_SECTOR_MAP = {
    "1": "水産・農林業",
    "2": "鉱業",
    "3": "建設業",
    "4": "食料品",
    "5": "繊維製品",
    "6": "輸送用機器",
    "7": "卸売業",
    "8": "銀行業",
    "9": "電気機器",
    "10": "サービス業",
}

LEGACY_NAME_MAP = {
    "電機・精密": "電気機器",
    "自動車・輸送機": "輸送用機器",
    "商社・卸売": "卸売業",
    "銀行・金融": "銀行業",
    "サービス": "サービス業",
    "情報・通信": "情報・通信業",
    "素材・化学": "化学",
}

SECTOR_ALIAS = {
    "証券・商品先物取引業": "証券、商品先物取引業",
    "情報通信": "情報・通信業",
    "その他金融": "その他金融業",
    "化学工業": "化学",
    "小売": "小売業",
    "卸売": "卸売業",
    "医薬": "医薬品",
    "海運": "海運業",
    "空運": "空運業",
    "陸運": "陸運業",
    "不動産": "不動産業",
}

def normalize_sector_code(c: str) -> str:
    c = clean_text(c)
    return str(int(c)) if c.isdigit() else ""

def normalize_sector_name(s: str) -> str:
    s = clean_text(s)
    if not s:
        return ""
    if s in CANON_SECTORS:
        return s
    if s in LEGACY_NAME_MAP:
        return LEGACY_NAME_MAP[s]
    for k, v in SECTOR_ALIAS.items():
        if k in s or s in k:
            return v
    for name in CANON_SECTORS:
        if s in name or name in s:
            return name
    return s

def decide_sector_fields(raws: List[str], code: str, name: str) -> Tuple[Optional[str], Optional[str]]:
    numeric_candidate = ""
    name_candidate = ""

    for v in raws:
        v = clean_text(v)
        if not v:
            continue
        if v.isdigit():
            numeric_candidate = normalize_sector_code(v)
        else:
            name_candidate = normalize_sector_name(v)

    # 両方あり→日本語優先
    if name_candidate and numeric_candidate:
        code_fix = REV_SECTOR_MAP.get(name_candidate)
        return (code_fix or numeric_candidate), name_candidate
    if name_candidate:
        code_fix = REV_SECTOR_MAP.get(name_candidate)
        return (code_fix or None), name_candidate
    if numeric_candidate:
        # 旧フォーマット補正
        if numeric_candidate in LEGACY_SECTOR_MAP:
            n = LEGACY_SECTOR_MAP[numeric_candidate]
            return REV_SECTOR_MAP.get(n), n
        return numeric_candidate, SECTOR_CODE_MAP.get(numeric_candidate)

    # ETF補正
    if (code.startswith(("13", "14", "15", "16")) or "ETF" in name or "ETN" in name):
        return None, "ETF/ETN"
    return None, None

# ---------- 取得と読込 ----------
def _download_jpx_bytes(url: str) -> bytes:
    headers = {"User-Agent": HTTP_UA}
    r = requests.get(url, headers=headers, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    return r.content

def _read_any_table_from_bytes(data: bytes) -> pd.DataFrame:
    xls = pd.ExcelFile(io.BytesIO(data))
    frames: List[pd.DataFrame] = []

    for sheet in xls.sheet_names:
        try:
            df = xls.parse(sheet_name=sheet, dtype=str)
            if df.empty:
                continue
            code_col = pick_code_col(df)
            name_col = pick_name_col(df)
            sector_cols = find_sector_candidates(df)
            if not code_col or not name_col:
                continue

            sub = df[[c for c in [code_col, name_col] + sector_cols if c]].copy()
            for c in sub.columns:
                sub[c] = sub[c].map(clean_text)
            sub = sub[sub[code_col].str.fullmatch(r"\d{4,5}")]

            rows = []
            for _, r in sub.iterrows():
                code = clean_text(r[code_col])
                nm = clean_text(r[name_col])
                raws = [clean_text(r[c]) for c in sector_cols] if sector_cols else []
                sc, sn = decide_sector_fields(raws, code, nm)
                rows.append(dict(code=code, name=nm, sector_code=sc, sector_name=sn))
            if rows:
                frames.append(pd.DataFrame(rows))
        except Exception:
            continue

    if not frames:
        raise RuntimeError("JPXマスタを表形式として読み取れませんでした。")
    out = pd.concat(frames, ignore_index=True)
    out = out.drop_duplicates(subset=["code"], keep="last")
    return out

# ---------- DB反映 ----------
@dataclass
class MasterStats:
    upserted: int
    touched_codes: int

def refresh_master(source_url: Optional[str] = None) -> MasterStats:
    url = source_url or DEFAULT_JPX_XLS_URL
    data = _download_jpx_bytes(url)
    table = _read_any_table_from_bytes(data)

    upserted = 0
    touched = 0
    with transaction.atomic():
        for _, r in table.iterrows():
            code = clean_text(r["code"])
            name = clean_text(r["name"])
            sector_code = r.get("sector_code")
            sector_name = r.get("sector_name")
            obj, created = StockMaster.objects.update_or_create(
                code=code,
                defaults=dict(
                    name=name,
                    sector_code=sector_code or None,
                    sector_name=sector_name or None,
                ),
            )
            upserted += 1 if created else 0
            touched += 1
    return MasterStats(upserted=upserted, touched_codes=touched)