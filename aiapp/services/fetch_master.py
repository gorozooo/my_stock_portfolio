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
# 恒久対応版：JPX添付Excelを直接DLし、全シートを柔軟に解析して
# code / name / sector_code / sector_name を更新する。
# ・市場区分は扱わない（不要要件）
# ・列名は日本語/揺れに対応して自動検出
# ・不可視文字/ゼロ幅/私用領域などを除去
# ・セクターは日本語名を優先で正規化し、数値が来た場合はそのままsector_codeに保存
# =========================================================

# 直リンク（添付ファイル）: 既存の portfolio 側と同じURL
DEFAULT_JPX_XLS_URL = os.getenv(
    "AIAPP_JPX_URL",
    "https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xls"
)

# HTTP
HTTP_TIMEOUT = float(os.getenv("AIAPP_HTTP_TIMEOUT", "60"))
HTTP_UA = os.getenv(
    "AIAPP_HTTP_UA",
    "Mozilla/5.0 (compatible; MyStockPortfolioBot/1.0; +https://example.invalid)"
)

# ---- 文字クレンジング -------------------------------------------------------
def clean_text(s: Optional[str]) -> str:
    if s is None:
        return ""
    s = unicodedata.normalize("NFKC", str(s))
    # 私用領域・ゼロ幅・制御文字を除去
    s = re.sub(r"[\uE000-\uF8FF\u200B-\u200D\u2060\uFEFF]", "", s)
    s = re.sub(r"[\x00-\x1F\x7F]", "", s)
    return s.strip()

# ---- 列名の自動検出（揺れに強く） -------------------------------------------
CODE_KEYS   = {"code", "ｺｰﾄﾞ", "コード", "銘柄コード"}
NAME_KEYS   = {"name", "銘柄名"}
SECTOR_KEYS = {"業種", "業種名", "33業種", "業種分類", "sector"}

def detect_columns(df: pd.DataFrame) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    # 小文字へ正規化したインデックスを作る
    low_map = {c: clean_text(c).lower() for c in df.columns}

    def pick(keys: Iterable[str]) -> Optional[str]:
        for raw, low in low_map.items():
            for k in keys:
                if k in low:
                    return raw
        return None

    return pick(CODE_KEYS), pick(NAME_KEYS), pick(SECTOR_KEYS)

# ---- セクター正規化（日本語名を“できるだけ”揃える） --------------------------
# ここでは名前の正規化のみ確実に実施し、数値コードは来たものを優先して保持する方針。
# ※ JPXの公開物は表記揺れがあるため、部分一致で丸める。
# ※ 「sector_code」はJPX由来の数値が来たときだけ採用（なければ空のまま）
CANON_SECTORS: List[str] = [
    "水産・農林業", "食料品", "建設業", "繊維製品", "パルプ・紙", "化学", "医薬品",
    "石油・石炭製品", "ゴム製品", "ガラス・土石製品", "鉄鋼", "非鉄金属", "金属製品",
    "機械", "電気機器", "輸送用機器", "精密機器", "その他製品", "電気・ガス業",
    "陸運業", "海運業", "空運業", "倉庫・運輸関連業", "情報・通信業",
    "卸売業", "小売業", "銀行業", "証券、商品先物取引業", "保険業",
    "その他金融業", "不動産業", "サービス業",
    # ETF/ETN は便宜上ここに含める（JPX側では別区分）
    "ETF/ETN",
]

# 表記揺れ→正規名 への簡易辞書（必要に応じて拡張）
SECTOR_ALIAS: Dict[str, str] = {
    "証券・商品先物取引業": "証券、商品先物取引業",
    "証券・商品先物": "証券、商品先物取引業",
    "情報通信": "情報・通信業",
    "電気機器": "電気機器",
    "その他製品": "その他製品",
    "その他金融": "その他金融業",
    "小売": "小売業",
    "卸売": "卸売業",
    "サービス": "サービス業",
    "医薬": "医薬品",
    "海運": "海運業",
    "空運": "空運業",
    "陸運": "陸運業",
    "不動産": "不動産業",
    "化学工業": "化学",
}

def normalize_sector_name(s: str) -> str:
    s = clean_text(s)
    if not s:
        return ""
    # 完全一致
    if s in CANON_SECTORS:
        return s
    # エイリアス
    for k, v in SECTOR_ALIAS.items():
        if k in s or s in k:
            return v
    # 部分一致で一番長い一致を採用
    best = ""
    for name in CANON_SECTORS:
        if s in name or name in s:
            if len(name) > len(best):
                best = name
    return best or s  # 見つからなければ原文のまま（後段で可視化）

# ---- JPX Excel の取得と読込 --------------------------------------------------
def _download_jpx_bytes(url: str) -> bytes:
    headers = {"User-Agent": HTTP_UA}
    r = requests.get(url, headers=headers, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    return r.content

def _read_any_table_from_bytes(data: bytes) -> pd.DataFrame:
    # xls/xlsx どちらでも受け付ける。全シートを総なめして結合。
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
            # 解析できないシートはスキップ
            continue

    if not frames:
        raise RuntimeError("JPXマスタを表形式として読み取れませんでした。")

    df = pd.concat(frames, ignore_index=True)
    # クレンジング
    for c in df.columns:
        df[c] = df[c].map(clean_text)

    # コードは 4〜5桁の数字だけ許容
    df = df[df["code"].str.fullmatch(r"\d{4,5}")]
    # 重複コードは後勝ち
    df = df.drop_duplicates(subset=["code"], keep="last")

    # 欠損列を補完
    if "sector" not in df.columns:
        df["sector"] = ""

    # セクター名の正規化（日本語）
    df["sector_name"] = df["sector"].map(normalize_sector_name)

    # もし sector が数値（"650" など）だけなら、それを sector_code として保持
    def pick_sector_code(x: str) -> str:
        x = clean_text(x)
        return x if x.isdigit() else ""

    df["sector_code"] = df["sector"].map(pick_sector_code)

    # 出力整形
    out = df[["code", "name", "sector_code", "sector_name"]].copy()
    return out

# ---- DB 反映 -----------------------------------------------------------------
@dataclass
class MasterStats:
    upserted: int
    touched_codes: int

def refresh_master(source_url: Optional[str] = None) -> MasterStats:
    """
    JPX添付Excelを直接ダウンロードして解析し、StockMaster を upsert。
    市場区分は扱わない。
    """
    url = source_url or DEFAULT_JPX_XLS_URL
    data = _download_jpx_bytes(url)
    table = _read_any_table_from_bytes(data)

    upserted = 0
    touched = 0

    with transaction.atomic():
        for _, r in table.iterrows():
            code = clean_text(r["code"])
            name = clean_text(r["name"])
            sector_code = clean_text(r.get("sector_code", ""))
            sector_name = clean_text(r.get("sector_name", "")) or None

            # ETF/ETN（銘柄名やコード帯で推定）→ sector_nameのみ明示
            if not sector_name:
                if "ETF" in name or "ETN" in name or code.startswith(("13", "14", "15", "16")):
                    sector_name = "ETF/ETN"

            obj, created = StockMaster.objects.update_or_create(
                code=code,
                defaults=dict(
                    name=name,
                    sector_code=sector_code or None,
                    sector_name=sector_name,
                ),
            )
            upserted += 1 if created else 0
            touched += 1

    return MasterStats(upserted=upserted, touched_codes=touched)