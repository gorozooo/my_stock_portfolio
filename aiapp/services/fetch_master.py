# -*- coding: utf-8 -*-
"""
aiapp.services.fetch_master
JPX公式ページから最新の「東証上場銘柄一覧（Excel）」を自動取得し、
全シート（内国株/グロース/ETF/ETN/REIT/外国株 等）をマージして
code / name / sector_code / sector_name に正規化 → CSV保存 → DBへ反映します。

対応:
- .xlsx / .xls / .csv すべて自動判定
  * .xls は libreoffice があれば自動変換（無ければエラー表示）
- 列名ゆらぎを網羅（和英、別名、部分一致）
- 33業種コード⇄名称の相互補完（"50" / "050" / "0050" / "50.0" を吸収）
- ETF/ETN/REIT/外国株など、業種が空のケースはカテゴリ名を補完
- SQLiteロック対策: WAL + busy_timeout、bulk_create / bulk_update

依存:
    pip install requests pandas openpyxl
    # .xls対応（任意・あると便利）
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
    # モデルは sector_code / sector_name が望ましいが、
    # 既存環境に sector33 しか無くても動くように後方互換で書き込みを吸収する
    from aiapp.models.master import StockMaster  # 推奨の配置
except Exception:
    from aiapp.models import StockMaster  # 既存互換


# ===== 設定（デフォルト） =====
MEDIA_ROOT    = getattr(settings, "MEDIA_ROOT", "media")
MASTER_DIR    = getattr(settings, "AIAPP_MASTER_DIR", os.path.join("aiapp", "master"))
MASTER_PAGE   = getattr(settings, "AIAPP_MASTER_PAGE", "https://www.jpx.co.jp/markets/statistics-equities/misc/01.html")
MASTER_URL    = getattr(settings, "AIAPP_MASTER_URL", None)  # 直リンクまたはローカルパス
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

# ===== 列名ゆらぎ候補 =====
NAME_KEYS = [
    "銘柄名", "会社名", "名称", "name", "Name", "COMPANY", "Company",
]
CODE_KEYS = [
    "コード", "証券コード", "code", "Code", "SC", "銘柄コード",
]
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
KIND_KEYS = [
    "市場区分", "上場区分", "区分", "種類", "分類", "種別", "Type", "Category",
]


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
    return data[:4] == b"PK\x03\x04"  # ZIP

def _is_xls(data: bytes) -> bool:
    return data[:8] == b"\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1"  # OLE2

def _find_excel_url_from_page(page_url: str) -> str | None:
    r = requests.get(page_url, timeout=30)
    r.raise_for_status()
    html = r.text
    m = re.search(r'href="([^"]+\.xlsx)"', html, re.IGNORECASE)
    if not m:
        m = re.search(r'href="([^"]+\.xls)"', html, re.IGNORECASE)
    if not m:
        return None
    return _abs_url(page_url, m.group(1))

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
        data = tmp_out.read_bytes()
        return data
    finally:
        for p in (tmp_in, tmp_out):
            try:
                os.remove(p)
            except OSError:
                pass

def _read_all_sheets(excel_bytes: bytes) -> List[pd.DataFrame]:
    """Excelバイナリから全シートを読み出す（注意書き等は自動スキップ）。"""
    data = excel_bytes
    if _looks_html(data):
        raise RuntimeError("Got HTML instead of Excel (login/redirect?)")

    # .xls → .xlsx（変換）
    if _is_xls(data):
        try:
            data = _xls_to_xlsx_bytes(data)
        except Exception as e:
            raise RuntimeError("XLSを開けません。AIAPP_MASTER_URL を .xlsx に変更するか、libreoffice を導入してください。") from e

    with pd.ExcelFile(io.BytesIO(data), engine="openpyxl") as xf:
        out: List[pd.DataFrame] = []
        for sh in xf.sheet_names:
            try:
                df = xf.parse(sh)
                if df is not None and not df.empty:
                    out.append(df)
            except Exception:
                continue
    if not out:
        raise RuntimeError("Excelの有効なシートを読み出せませんでした。")
    return out

def _pick_col(cols: Iterable[str], keys: List[str]) -> Optional[str]:
    low = {str(c).lower(): c for c in cols}
    for k in keys:
        kk = k.lower()
        for lc, orig in low.items():
            if kk in lc:
                return orig
    return None

def _canon_code(s: str | None) -> str | None:
    if not s:
        return None
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

def _normalize_one(df: pd.DataFrame, sheet_name: str = "") -> pd.DataFrame | None:
    """1シートを code/name/sector_code/sector_name に正規化。ダメなら None。"""
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

    # sector は2列（code/name）を独立に拾って相互補完
    sc = df[col_scd].astype(str) if col_scd else pd.Series([None] * len(df))
    sn = df[col_snm].astype(str) if col_snm else pd.Series([None] * len(df))

    sc2: List[Optional[str]] = []
    sn2: List[Optional[str]] = []
    for a, b in zip(sc, sn):
        c_fix, n_fix = _canon_sector(a, b)
        sc2.append(c_fix); sn2.append(n_fix)
    out["sector_code"] = sc2
    out["sector_name"] = sn2

    # ETF/REIT/外国株など sector が空の場合はカテゴリ補完
    if col_kind:
        kinds = df[col_kind].astype(str).map(_nfkc).str.strip().fillna("")
        hint = None
        if "ETF" in (sheet_name.upper() if sheet_name else ""): hint = "ETF/ETN"
        elif "REIT" in (sheet_name.upper() if sheet_name else ""): hint = "REIT"
        for i, (scv, snv, kv) in enumerate(zip(out["sector_code"], out["sector_name"], kinds)):
            if not scv and not snv:
                if "REIT" in kv.upper():
                    out.at[i, "sector_name"] = "REIT"
                elif "ETF" in kv.upper() or "ETN" in kv.upper():
                    out.at[i, "sector_name"] = "ETF/ETN"
                elif hint:
                    out.at[i, "sector_name"] = hint

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
        raise ValueError("有効なシートが見つかりません（code/name/sector_* が検出できず）")
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

# ===== DB upsert（後方互換を吸収） =====
def _upsert_db(df: pd.DataFrame) -> int:
    """
    - 新規は bulk_create(ignore_conflicts=True)
    - 既存は bulk_update
    - モデルに sector_code/sector_name が無い（古い）環境でも、sector33 に連結して保存できる
    """
    # 既存列を確認
    has_sc = hasattr(StockMaster, "sector_code")
    has_sn = hasattr(StockMaster, "sector_name")
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
            obj = StockMaster(code=r["code"], name=r["name"])
            if has_sc: obj.sector_code = r.get("sector_code")
            if has_sn: obj.sector_name = r.get("sector_name")
            if not (has_sc or has_sn) and has_s33:
                # 後方互換：sector33 だけの古いモデル
                s33 = r.get("sector_name") or r.get("sector_code") or ""
                obj.sector33 = s33
            to_create.append(obj)
        if to_create:
            StockMaster.objects.bulk_create(to_create, ignore_conflicts=True, batch_size=500)
            created_count = len(to_create)

        # 既存更新
        upd_rows = df[df["code"].isin(existing)]
        if not upd_rows.empty:
            inst_map: Dict[str, StockMaster] = {
                o.code: o for o in StockMaster.objects.filter(code__in=upd_rows["code"].tolist())
            }
            for _, r in upd_rows.iterrows():
                o = inst_map.get(r["code"])
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
            StockMaster.objects.bulk_update(inst_map.values(), fields, batch_size=500)

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
            if _looks_html(data):
                raise RuntimeError("指定URLがHTMLを返しています。直リンクを指定してください。")
            if url_or_path.lower().endswith((".xlsx", ".xls")) or _is_xlsx(data) or _is_xls(data):
                # Excel系
                if _is_xls(data):
                    data = _xls_to_xlsx_bytes(data)  # libreoffice 必須
                with pd.ExcelFile(io.BytesIO(data), engine="openpyxl") as xf:
                    dfs = [xf.parse(sh) for sh in xf.sheet_names]
                    dfs = [d for d in dfs if d is not None and not d.empty]
                merged = _normalize_all(dfs, sheet_names=xf.sheet_names)
            else:
                # CSV扱い
                bio = io.BytesIO(data)
                ok = None
                for enc in CSV_ENCODING_TRY:
                    try:
                        bio.seek(0)
                        ok = pd.read_csv(bio, encoding=enc)
                        break
                    except Exception:
                        continue
                if ok is None:
                    raise RuntimeError("CSVのデコードに失敗しました。")
                df = ok
                # CSVは列確定が難しいので先頭3列を code/name/sector_name と仮定し、補正する
                df2 = df.copy()
                df2.columns = [str(c) for c in df2.columns]
                # 柔軟に拾える場合は正規化を通す
                merged = _normalize_one(df2) or df2.iloc[:, :3].rename(
                    columns={df2.columns[0]: "code", df2.columns[1]: "name", df2.columns[2]: "sector_name"}
                )
                merged["code"] = merged["code"].map(_canon_code)
                merged = merged.dropna(subset=["code", "name"]).drop_duplicates(subset=["code"])
        else:
            # ローカルパス
            ext = os.path.splitext(url_or_path)[1].lower()
            if ext in (".xlsx", ".xls"):
                data = open(url_or_path, "rb").read()
                dfs = _read_all_sheets(data)
                with pd.ExcelFile(io.BytesIO(data), engine="openpyxl") as xf:
                    sheet_names = xf.sheet_names
                merged = _normalize_all(dfs, sheet_names)
            else:
                ok = None
                for enc in CSV_ENCODING_TRY:
                    try:
                        ok = pd.read_csv(url_or_path, encoding=enc)
                        break
                    except Exception:
                        continue
                if ok is None:
                    raise RuntimeError(f"CSV read failed: {url_or_path}")
                merged = _normalize_one(ok) or ok.iloc[:, :3].rename(
                    columns={ok.columns[0]: "code", ok.columns[1]: "name", ok.columns[2]: "sector_name"}
                )
                merged["code"] = merged["code"].map(_canon_code)
                merged = merged.dropna(subset=["code", "name"]).drop_duplicates(subset=["code"])
    else:
        # 2) 一覧ページをスクレイプしてExcelリンクを自動検出
        excel_url = _find_excel_url_from_page(MASTER_PAGE)
        if not excel_url:
            raise RuntimeError("JPXページからExcelリンクを見つけられませんでした。")
        r = requests.get(excel_url, timeout=30)
        r.raise_for_status()
        data = r.content
        dfs = _read_all_sheets(data)
        with pd.ExcelFile(io.BytesIO(data), engine="openpyxl") as xf:
            sheet_names = xf.sheet_names
        merged = _normalize_all(dfs, sheet_names)

    _save_csv(merged)
    n_new = _upsert_db(merged)
    return n_new