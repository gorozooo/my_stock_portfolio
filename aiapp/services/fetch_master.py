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
# ・市場区分は扱わない（要求どおり）
# ・列名の揺れに強い自動検出（業種=数値/日本語を同時推定）
# ・不可視/ゼロ幅/ダミー記号を除去
# ・数値コード↔日本語名を相互補完（不一致時は日本語名を優先）
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

def pick_code_col(df: pd.DataFrame) -> Optional[str]:
    low = {c: clean_text(c).lower() for c in df.columns}
    for raw, lowc in low.items():
        for k in CODE_KEYS:
            if k in lowc:
                return raw
    return None

def pick_name_col(df: pd.DataFrame) -> Optional[str]:
    low = {c: clean_text(c).lower() for c in df.columns}
    for raw, lowc in low.items():
        for k in NAME_KEYS:
            if k in lowc:
                return raw
    return None

def find_sector_candidates(df: pd.DataFrame) -> List[str]:
    low = {c: clean_text(c).lower() for c in df.columns}
    out = []
    for raw, lowc in low.items():
        if any(k in lowc for k in SECTOR_KEYS):
            out.append(raw)
    return out

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
REV_SECTOR_MAP: Dict[str, str] = {v: k for k, v in SECTOR_CODE_MAP.items()}
CANON_SECTORS: List[str] = list(SECTOR_CODE_MAP.values())

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

def normalize_sector_code(c: str) -> str:
    c = clean_text(c)
    if not c or not c.isdigit():
        return ""
    return str(int(c))  # 例: "0650"→"650"

def normalize_sector_name(s: str) -> str:
    s = clean_text(s)
    if not s:
        return ""
    if s in CANON_SECTORS:
        return s
    for k, v in SECTOR_ALIAS.items():
        if k in s or s in k:
            return v
    # 部分一致（最長一致）
    best = ""
    for name in CANON_SECTORS:
        if s in name or name in s:
            if len(name) > len(best):
                best = name
    return best or s

def decide_sector_fields(raws: List[str], code: str, name: str) -> Tuple[Optional[str], Optional[str]]:
    """
    業種候補の複数セルから sector_code / sector_name を決定。
    - 数値優勢セル→コード候補
    - 日本語優勢セル→名称候補
    - 両方あれば名称優先で整合（逆引きでコード補完）
    - どちらも無ければ ETF 判定（空 & ETF/ETN らしさがある）
    """
    numeric_candidate = ""
    name_candidate = ""

    for v in raws:
        v = clean_text(v)
        if not v:
            continue
        if v.isdigit():
            c = normalize_sector_code(v)
            if c:
                numeric_candidate = c
        else:
            n = normalize_sector_name(v)
            if n:
                name_candidate = n

    if name_candidate and numeric_candidate:
        # 名前を正とし、コードは逆引き
        code_fix = REV_SECTOR_MAP.get(name_candidate)
        return (code_fix if code_fix else numeric_candidate), name_candidate

    if name_candidate:
        code_fix = REV_SECTOR_MAP.get(name_candidate)
        return (code_fix if code_fix else None), name_candidate

    if numeric_candidate:
        return numeric_candidate, SECTOR_CODE_MAP.get(numeric_candidate, None)

    # 何も取れない場合はETF推定
    if (code.startswith(("13", "14", "15", "16")) or "ETF" in name or "ETN" in name):
        return None, "ETF/ETN"

    return None, None

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

            code_col = pick_code_col(df)
            name_col = pick_name_col(df)
            sector_cols = find_sector_candidates(df)

            if not code_col or not name_col:
                continue

            # 業種候補は複数あって良い。まず全部集める
            cols = [code_col, name_col] + sector_cols
            cols = [c for c in cols if c]  # None除去
            sub = df[cols].copy()

            # 正規化
            for c in sub.columns:
                sub[c] = sub[c].map(clean_text)

            # 銘柄コードの形に絞る
            sub = sub[sub[code_col].str.fullmatch(r"\d{4,5}")]
            if sub.empty:
                continue

            # 行ごとに sector を決定
            out_rows = []
            for _, r in sub.iterrows():
                code = clean_text(r[code_col])
                name = clean_text(r[name_col])
                raw_list = [clean_text(r[c]) for c in sector_cols] if sector_cols else []
                sc, sn = decide_sector_fields(raw_list, code, name)
                out_rows.append(dict(code=code, name=name, sector_code=sc, sector_name=sn))

            if out_rows:
                frames.append(pd.DataFrame(out_rows, columns=["code", "name", "sector_code", "sector_name"]))
        except Exception:
            continue

    if not frames:
        raise RuntimeError("JPXマスタを表形式として読み取れませんでした。")

    out = pd.concat(frames, ignore_index=True)

    # 重複コードは最後勝ち
    out = out.drop_duplicates(subset=["code"], keep="last")
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