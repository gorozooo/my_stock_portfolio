# -*- coding: utf-8 -*-
"""
aiapp.services.fetch_master
JPX公式の「東証上場銘柄一覧（Excel）」を自動取得し、
全シート（内国株/グロース/ETF/ETN/REIT/外国株 等）をマージして
code / name / sector_code / sector_name に正規化 → CSV保存 → DB反映します。

ポイント
- .xlsx / .xls / .csv すべて自動判定（xlsは libreoffice 変換に対応：任意）
- 列名ゆらぎを広く吸収（和英・別名）
- Excel失敗時は必ずCSVにフォールバック（BadZipFile等を吸収）
- 33業種コード⇄名称の相互補完（"50", "050", "0050", "50.0" 等すべて対応）
- ETF/ETN/REIT/外国株など sector が空のケースはカテゴリ名で補完
- SQLite ロック対策（WAL + busy_timeout、bulk_create / bulk_update）

依存:
    pip install requests pandas openpyxl
    # .xls対応（任意）
    # sudo apt update && sudo apt install -y libreoffice
"""
from __future__ import annotations

import io
import os
import re
import unicodedata
import datetime as dt
from typing import Dict, Iterable, List, Optional, Tuple

import pandas as pd
import requests
from django.conf import settings
from django.db import connection, transaction

try:
    # 推奨：aiapp/models/master.py
    from aiapp.models.master import StockMaster
except Exception:
    # 後方互換：aiapp/models.py にある場合
    from aiapp.models import StockMaster


# ===== 設定 =====
MEDIA_ROOT    = getattr(settings, "MEDIA_ROOT", "media")
MASTER_DIR    = getattr(settings, "AIAPP_MASTER_DIR", os.path.join("aiapp", "master"))
MASTER_PAGE   = getattr(settings, "AIAPP_MASTER_PAGE",
                        "https://www.jpx.co.jp/markets/statistics-equities/misc/01.html")
MASTER_URL    = getattr(settings, "AIAPP_MASTER_URL", None)  # 直リンク or ローカルパス
CSV_ENCODING_TRY = ("utf-8", "cp932", "shift_jis", "utf-8-sig")

# ===== 33業種マップ（主要） =====
JPX33_MAP: Dict[str, str] = {
    "005": "水産・農林業", "010": "鉱業", "020": "建設業", "025": "食料品", "030": "繊維製品",
    "035": "パルプ・紙", "040": "化学", "045": "医薬品", "050": "石油・石炭製品", "055": "ゴム製品",
    "060": "ガラス・土石製品", "065": "鉄鋼", "070": "非鉄金属", "075": "金属製品", "080": "機械",
    "085": "電気機器", "090": "輸送用機器", "095": "精密機器", "100": "その他製品", "105": "電気・ガス業",
    "110": "陸運業", "115": "海運業", "120": "空運業", "125": "倉庫・運輸関連業", "130": "情報・通信業",
    "135": "卸売業", "140": "小売業", "145": "銀行業", "150": "証券・商品先物取引業",
    "155": "保険業", "160": "その他金融業", "165": "不動産業", "170": "サービス業",
}

# ===== 列名候補 =====
NAME_KEYS = ["銘柄名", "会社名", "名称", "name", "Name", "COMPANY", "Company"]
CODE_KEYS = ["コード", "証券コード", "code", "Code", "SC", "銘柄コード"]
SECTOR_CODE_KEYS = [
    "33業種コード", "業種コード", "セクターコード",
    "sector_code", "industry33_code", "industry_code",
    "SectorCode", "CategoryCode",
]
SECTOR_NAME_KEYS = [
    "33業種名", "業種名", "セクター", "セクター名",
    "sector_name", "industry33_name", "industry_name",
    "Sector", "Category", "CategoryName",
]
KIND_KEYS = ["市場区分", "上場区分", "区分", "種類", "分類", "種別", "Type", "Category"]


# ===== ユーティリティ =====
def _ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)

def _nfkc(s: str) -> str:
    try:
        return unicodedata.normalize("NFKC", s or "")
    except Exception:
        return s or ""

def _abs_url(page_url: str, href: str) -> str:
    if href.startswith("http"):
        return href
    from urllib.parse import urljoin
    return urljoin(page_url, href)

def _looks_html(data: bytes) -> bool:
    head = (data[:256] or b"").lower()
    return b"<html" in head or b"<!doctype html" in head

def _is_xlsx(data: bytes) -> bool:
    return data[:4] == b"PK\x03\x04"  # ZIPヘッダ

def _is_xls(data: bytes) -> bool:
    return data[:8] == b"\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1"  # OLE2

def _find_excel_url_from_page(page_url: str) -> str | None:
    r = requests.get(page_url, timeout=30)
    r.raise_for_status()
    html = r.text
    m = re.search(r'href="([^"]+\.xlsx)"', html, re.IGNORECASE)
    if not m:
        m = re.search(r'href="([^"]+\.xls)"', html, re.IGNORECASE)
    return _abs_url(page_url, m.group(1)) if m else None

def _xls_to_xlsx_bytes(binary: bytes) -> bytes:
    """libreoffice があれば .xls → .xlsx 変換。無ければ例外。"""
    import tempfile, subprocess
    from pathlib import Path
    tmp_in = Path(tempfile.mkstemp(suffix=".xls")[1])
    tmp_out = tmp_in.with_suffix(".xlsx")
    tmp_in.write_bytes(binary)
    try:
        subprocess.run(
            ["libreoffice", "--headless", "--convert-to", "xlsx", str(tmp_in), "--outdir", str(tmp_in.parent)],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        return tmp_out.read_bytes()
    finally:
        for p in (tmp_in, tmp_out):
            try:
                os.remove(p)
            except OSError:
                pass

def _pick_col(cols: Iterable[str], keys: List[str]) -> Optional[str]:
    low = {str(c).lower(): c for c in cols}
    for k in keys:
        kk = k.lower()
        for lc, orig in low.items():
            if kk in lc:
                return orig
    return None

def _canon_code(s: str | None) -> Optional[str]:
    if not s: return None
    s = _nfkc(str(s)).strip()
    m = re.search(r"(\d{4,5})", s)
    return m.group(1) if m else None

def _canon_sector(code_val: Optional[str], name_val: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """33業種コード/名称を相互補完（コードは3桁化）。"""
    if name_val:
        name_val = _nfkc(str(name_val)).strip() or None
    if code_val:
        raw = _nfkc(str(code_val)).strip()
        if raw.isdigit():
            code_val = (raw[-3:] if len(raw) >= 3 else raw.zfill(3))
        else:
            m = re.match(r"^\s*(\d+)", raw)
            code_val = m.group(1).zfill(3) if m else None
    if code_val and not name_val:
        name_val = JPX33_MAP.get(code_val)
    if name_val and not code_val:
        for k, v in JPX33_MAP.items():
            if v == name_val:
                code_val = k
                break
    return code_val, name_val

def _normalize_one(df: pd.DataFrame, sheet_name: str = "") -> Optional[pd.DataFrame]:
    """1シートを code/name/sector_code/sector_name に正規化。"""
    if df is None or df.empty:
        return None

    cols = list(df.columns)
    col_code = _pick_col(cols, CODE_KEYS)
    col_name = _pick_col(cols, NAME_KEYS)
    col_scd  = _pick_col(cols, SECTOR_CODE_KEYS)
    col_snm  = _pick_col(cols, SECTOR_NAME_KEYS)
    col_kind = _pick_col(cols, KIND_KEYS)

    if not (col_code and col_name):
        return None

    out = pd.DataFrame()
    out["code"] = df[col_code].map(_canon_code)
    out["name"] = df[col_name].astype(str).map(_nfkc).str.strip()

    sc = df[col_scd].astype(str) if col_scd else pd.Series([None] * len(df))
    sn = df[col_snm].astype(str) if col_snm else pd.Series([None] * len(df))

    sc2: List[Optional[str]] = []
    sn2: List[Optional[str]] = []
    for a, b in zip(sc, sn):
        c_fix, n_fix = _canon_sector(a, b)
        sc2.append(c_fix); sn2.append(n_fix)
    out["sector_code"] = sc2
    out["sector_name"] = sn2

    # ETF/REIT/外国株など sector が空のケースはカテゴリ/シート名で補完
    if col_kind:
        kinds = df[col_kind].astype(str).map(_nfkc).str.strip().fillna("")
    else:
        kinds = pd.Series([""] * len(out))
    hint = (sheet_name or "").upper()
    for i in range(len(out)):
        if not out.at[i, "sector_code"] and not out.at[i, "sector_name"]:
            kv = kinds.iloc[i].upper()
            if "REIT" in kv or "REIT" in hint:
                out.at[i, "sector_name"] = "REIT"
            elif "ETF" in kv or "ETN" in kv or "ETF" in hint:
                out.at[i, "sector_name"] = "ETF/ETN"

    out = out.dropna(subset=["code", "name"])
    out = out.drop_duplicates(subset=["code"]).reset_index(drop=True)
    return out

def _normalize_all(dfs: List[pd.DataFrame], sheet_names: List[str]) -> pd.DataFrame:
    keep: List[pd.DataFrame] = []
    for df, sh in zip(dfs, sheet_names):
        norm = _normalize_one(df, sh)
        if norm is not None and not norm.empty:
            keep.append(norm)
    if not keep:
        raise ValueError("有効なシートが見つかりません（code/name/sector_* の検出に失敗）")
    merged = pd.concat(keep, axis=0, ignore_index=True)
    merged = merged.drop_duplicates(subset=["code"]).reset_index(drop=True)
    return merged

def _save_csv(df: pd.DataFrame) -> str:
    out_dir = os.path.join(MEDIA_ROOT, MASTER_DIR)
    _ensure_dir(out_dir)
    out_name = f"master_{dt.date.today():%Y%m%d}.csv"
    out_path = os.path.join(out_dir, out_name)
    df.to_csv(out_path, index=False, encoding="utf-8")
    return out_path


# ===== Excel/CSV 読み取りの統一入口 =====
def _read_excel_or_csv_bytes(data: bytes, hint_ext: Optional[str] = None) -> pd.DataFrame:
    """
    dataがExcel(.xlsx/.xls)なら全シートをマージ、ダメならCSVで読む。
    HTMLを受け取った場合は説明付きでエラーにする。
    """
    # HTMLなら明示的にエラー
    if _looks_html(data):
        raise RuntimeError("ExcelではなくHTMLが返されました。直リンクか、一覧ページの自動検出に切り替えてください。")

    # Excel判定
    try:
        excel_bytes = data
        if hint_ext and hint_ext.lower().endswith(".xls"):
            # ヒントが .xls の場合はまず変換を試みる
            try:
                excel_bytes = _xls_to_xlsx_bytes(excel_bytes)
            except Exception:
                # ヒント通りでなくとも続行（後段のxlsx判定に任せる）
                excel_bytes = data

        if _is_xls(excel_bytes):
            excel_bytes = _xls_to_xlsx_bytes(excel_bytes)

        if _is_xlsx(excel_bytes):
            with pd.ExcelFile(io.BytesIO(excel_bytes), engine="openpyxl") as xf:
                sheet_names = xf.sheet_names
                dfs = [xf.parse(sh) for sh in sheet_names]
                dfs = [d for d in dfs if d is not None and not d.empty]
            if not dfs:
                raise ValueError("Excel内に有効なシートが見つかりません。")
            return _normalize_all(dfs, sheet_names=sheet_names)

        # ここまで来たらExcelサイン無し → CSVにフォールバック
        # （BadZipFileなどの例外が発生する前にCSVを試す）
        bio = io.BytesIO(data)
        for enc in CSV_ENCODING_TRY:
            try:
                bio.seek(0)
                df = pd.read_csv(bio, encoding=enc)
                # 先頭3列を code/name/sector_name と仮定しつつ、正規化関数で補正
                merged = _normalize_one(df) or df.iloc[:, :3].rename(
                    columns={df.columns[0]: "code", df.columns[1]: "name", df.columns[2]: "sector_name"}
                )
                merged["code"] = merged["code"].map(_canon_code)
                merged = merged.dropna(subset=["code", "name"]).drop_duplicates(subset=["code"])
                return merged
            except Exception:
                continue
        raise RuntimeError("CSVとしても解読できませんでした。")
    except Exception as e:
        # 最後の防波堤：Excel解釈で落ちたらCSV再挑戦
        bio = io.BytesIO(data)
        for enc in CSV_ENCODING_TRY:
            try:
                bio.seek(0)
                df = pd.read_csv(bio, encoding=enc)
                merged = _normalize_one(df) or df.iloc[:, :3].rename(
                    columns={df.columns[0]: "code", df.columns[1]: "name", df.columns[2]: "sector_name"}
                )
                merged["code"] = merged["code"].map(_canon_code)
                merged = merged.dropna(subset=["code", "name"]).drop_duplicates(subset=["code"])
                print(f"[fetch_master] Excel失敗→CSVフォールバック成功（encoding={enc}） rows={len(merged)}")
                return merged
            except Exception:
                continue
        raise RuntimeError(f"ファイル判別に失敗しました: {e}")


# ===== DB upsert（後方互換を吸収） =====
def _upsert_db(df: pd.DataFrame) -> int:
    """
    - 新規は bulk_create(ignore_conflicts=True)
    - 既存は bulk_update
    - モデルに sector_code/sector_name が無い古い環境でも sector33 に連結して保存可能
    """
    has_sc  = hasattr(StockMaster, "sector_code")
    has_sn  = hasattr(StockMaster, "sector_name")
    has_s33 = hasattr(StockMaster, "sector33")

    with connection.cursor() as cur:
        try: cur.execute("PRAGMA journal_mode=WAL;")
        except Exception: pass
        try: cur.execute("PRAGMA busy_timeout=5000;")
        except Exception: pass

    codes = df["code"].tolist()
    created_count = 0

    with transaction.atomic():
        existing = set(StockMaster.objects.filter(code__in=codes).values_list("code", flat=True))

        # 新規
        new_rows = df[~df["code"].isin(existing)]
        to_create: List[StockMaster] = []
        for _, r in new_rows.iterrows():
            o = StockMaster(code=r["code"], name=r["name"])
            if has_sc: o.sector_code = r.get("sector_code")
            if has_sn: o.sector_name = r.get("sector_name")
            if not (has_sc or has_sn) and has_s33:
                s33 = r.get("sector_name") or r.get("sector_code") or ""
                o.sector33 = s33
            to_create.append(o)
        if to_create:
            StockMaster.objects.bulk_create(to_create, ignore_conflicts=True, batch_size=500)
            created_count = len(to_create)

        # 既存
        upd_rows = df[df["code"].isin(existing)]
        if not upd_rows.empty:
            mmap: Dict[str, StockMaster] = {
                o.code: o for o in StockMaster.objects.filter(code__in=upd_rows["code"].tolist())
            }
            for _, r in upd_rows.iterrows():
                o = mmap.get(r["code"])
                if not o: continue
                o.name = r["name"]
                if has_sc: o.sector_code = r.get("sector_code")
                if has_sn: o.sector_name = r.get("sector_name")
                if not (has_sc or has_sn) and has_s33:
                    s33 = r.get("sector_name") or r.get("sector_code") or ""
                    o.sector33 = s33
            fields = ["name"]
            if has_sc: fields.append("sector_code")
            if has_sn: fields.append("sector_name")
            if not (has_sc or has_sn) and has_s33: fields.append("sector33")
            StockMaster.objects.bulk_update(mmap.values(), fields, batch_size=500)

    return created_count


# ===== 公開API =====
def refresh_master(source_url: str | None = None) -> int:
    """
    最新Excel/CSVを取得 → 正規化 → CSV保存 → DB反映。
    優先順位: 引数source_url > settings.AIAPP_MASTER_URL > settings.AIAPP_MASTER_PAGE
    戻り値: 新規insert件数
    """
    url_or_path = source_url or MASTER_URL

    # 1) 明示指定（URL/ローカル）
    if url_or_path:
        if url_or_path.startswith("http"):
            r = requests.get(url_or_path, timeout=30)
            r.raise_for_status()
            data = r.content
            print(f"[fetch_master] GET {url_or_path} bytes={len(data)}")
            merged = _read_excel_or_csv_bytes(data, hint_ext=os.path.splitext(url_or_path)[1])
        else:
            ext = os.path.splitext(url_or_path)[1].lower()
            with open(url_or_path, "rb") as f:
                data = f.read()
            print(f"[fetch_master] READ {url_or_path} bytes={len(data)}")
            merged = _read_excel_or_csv_bytes(data, hint_ext=ext)
    else:
        # 2) 一覧ページをスクレイプしてExcel直リンクを自動検出
        excel_url = _find_excel_url_from_page(MASTER_PAGE)
        if not excel_url:
            raise RuntimeError("JPXページからExcelリンクを見つけられませんでした。")
        r = requests.get(excel_url, timeout=30)
        r.raise_for_status()
        data = r.content
        print(f"[fetch_master] SCRAPED {excel_url} bytes={len(data)}")
        merged = _read_excel_or_csv_bytes(data, hint_ext=os.path.splitext(excel_url)[1])

    # 保存＆DB反映
    _save_csv(merged)
    n_new = _upsert_db(merged)
    print(f"[fetch_master] upsert rows: {n_new} (total input={len(merged)})")
    return n_new