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
# code / name / sector_code / sector_name を恒久的に更新する。
# ・市場区分は扱わない
# ・列名の揺れに強い自動検出
# ・不可視/ゼロ幅/ダミー記号を除去
# ・数値コード→33業種名へ確実にマッピング
# ・ETF/ETNは業種が空/ダミー時に強制付与
# =========================================================

DEFAULT_JPX_XLS_URL = os.getenv(
    "AIAPP_JPX_URL",
    "https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xls",
)

HTTP_TIMEOUT = float(os.getenv("AIAPP_HTTP_TIMEOUT", "60"))
HTTP_UA = os.getenv(
    "AIAPP_HTTP_UA",
    "Mozilla/5.0 (compatible; MyStockPortfolioBot/1.0; +https://example.invalid)",
)

# ---------- 文字クレンジング ----------
DUMMY_TOKENS = {"-", "—", "－", "–", "―"}

def clean_text(s: Optional[str]) -> str:
    if s is None:
        return ""
    s = unicodedata.normalize("NFKC", str(s))
    # 私用領域・ゼロ幅・制御文字
    s = re.sub(r"[\uE000-\uF8FF\u200B-\u200D\u2060\uFEFF]", "", s)
    s = re.sub(r"[\x00-\x1F\x7F]", "", s)
    s = s.strip()
    if s in DUMMY_TOKENS:
        return ""
    return s

# ---------- 列名の自動検出 ----------
CODE_KEYS   = {"code", "ｺｰﾄﾞ", "コード", "銘柄コード"}
NAME_KEYS   = {"name", "銘柄名"}
SECTOR_KEYS = {"業種", "業種名", "33業種", "業種分類", "sector"}

def detect_columns(df: pd.DataFrame) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    low_map = {c: clean_text(c).lower() for c in df.columns}

    def pick(keys: Iterable[str]) -> Optional[str]:
        for raw, low in low_map.items():
            for k in keys:
                if k in low:
                    return raw
        return None

    return pick(CODE_KEYS), pick(NAME_KEYS), pick(SECTOR_KEYS)

# ---------- 33業種コード → 名称（JPX準拠） ----------
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
# 先頭ゼロや空白の揺れにも耐性
def normalize_sector_code(c: str) -> str:
    c = clean_text(c)
    if not c:
        return ""
    if not c.isdigit():
        return ""
    # 例: "0650" → "650" に
    return str(int(c))

# 名称側のゆれをざっくり吸収
SECTOR_ALIAS: Dict[str, str] = {
    "証券・商品先物取引業": "証券、商品先物取引業",
    "情報通信": "情報・通信業",
    "その他金融": "その他金融業",
    "化学工業": "化学",
    "小売": "小売業",
    "卸売": "卸売業",
    "サービス": "サービス業",
    "医薬": "医薬品",
    "海運": "海運業",
    "空運": "空運業",
    "陸運": "陸運業",
    "不動産": "不動産業",
}

CANON_SECTORS: List[str] = list(SECTOR_CODE_MAP.values())

def normalize_sector_name(s: str) -> str:
    s = clean_text(s)
    if not s:
        return ""
    if s in CANON_SECTORS:
        return s
    for k, v in SECTOR_ALIAS.items():
        if k in s or s in k:
            return v
    # 部分一致で最長一致
    best = ""
    for name in CANON_SECTORS:
        if s in name or name in s:
            if len(name) > len(best):
                best = name
    return best or s

def parse_sector_fields(raw_sector: str, code: str, name: str) -> Tuple[Optional[str], Optional[str]]:
    """
    JPXの業種セル（数値コード/日本語名/ダミー）から (sector_code, sector_name) を返す。
    ETF/ETN はここで sector_name='ETF/ETN' を付与。
    """
    rs = clean_text(raw_sector)
    # ETF 判定（業種が空 or ダミーのときに付与）
    if not rs and (code.startswith(("13", "14", "15", "16")) or "ETF" in name or "ETN" in name):
        return None, "ETF/ETN"

    # 数値コード → 名称
    sc = normalize_sector_code(rs)
    if sc:
        return sc, SECTOR_CODE_MAP.get(sc, None)

    # 日本語 → 正規名（必要ならここで逆引きも可：名称→コード）
    sn = normalize_sector_name(rs)
    # 逆引き（名称が一意ならコードも埋める）
    rev = {v: k for k, v in SECTOR_CODE_MAP.items()}
    sc2 = rev.get(sn)
    return (sc2 if sc2 else None), (sn if sn else None)

# ---------- JPX Excel の取得と読込 ----------
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
            if df is None or df.empty:
                continue
            code_col, name_col, sector_col = detect_columns(df)
            if not code_col or not name_col:
                continue
            cols = [code_col, name_col]
            if sector_col:
                cols.append(sector_col)
            sub = df[cols].copy()
            sub.rename(
                columns={
                    code_col: "code",
                    name_col: "name",
                    sector_col: "sector" if sector_col else None,
                },
                inplace=True,
            )
            frames.append(sub)
        except Exception:
            continue

    if not frames:
        raise RuntimeError("JPXマスタを表形式として読み取れませんでした。")

    df = pd.concat(frames, ignore_index=True)
    for c in df.columns:
        df[c] = df[c].map(clean_text)

    df = df[df["code"].str.fullmatch(r"\d{4,5}")]
    df = df.drop_duplicates(subset=["code"], keep="last")
    if "sector" not in df.columns:
        df["sector"] = ""

    # sector_code / sector_name を決定
    out_rows = []
    for _, r in df.iterrows():
        code = clean_text(r["code"])
        name = clean_text(r["name"])
        raw_sector = clean_text(r.get("sector", ""))

        sc, sn = parse_sector_fields(raw_sector, code, name)
        out_rows.append(dict(code=code, name=name, sector_code=sc, sector_name=sn))

    out = pd.DataFrame(out_rows, columns=["code", "name", "sector_code", "sector_name"])
    return out

# ---------- DB 反映 ----------
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
                    sector_code=sector_code if sector_code else None,
                    sector_name=sector_name if sector_name else None,
                ),
            )
            upserted += 1 if created else 0
            touched += 1

    return MasterStats(upserted=upserted, touched_codes=touched)