"""
aiapp.services.fetch_master
JPX公式ページから最新の「東証上場銘柄一覧（Excel）」を自動取得し、
全シート（内国株/グロース/ETF/ETN/REIT/外国株 等）をマージして
code/name/sector33 に正規化→CSV保存→DBへ反映します。

・.xlsx / .xls 両対応（.xls は libreoffice で一時変換→openpyxlで読む）
・SQLite ロック対策：WAL + busy_timeout、bulk_create/bulk_update で高速upsert
・settingsで URL 直指定や保存先変更可能

依存:
    pip install requests pandas openpyxl
    # .xls対応（サーバにlibreofficeが必要）
    sudo apt update && sudo apt install -y libreoffice
"""

from __future__ import annotations
import os
import re
import io
import datetime as dt
from typing import Dict, List

import pandas as pd
import requests
from django.conf import settings
from django.db import connection, transaction
from aiapp.models import StockMaster

# ---- settings defaults -------------------------------------------------------
DEFAULT_MEDIA = getattr(settings, "MEDIA_ROOT", "media")
MASTER_DIR = getattr(settings, "AIAPP_MASTER_DIR", os.path.join("aiapp", "master"))
MASTER_PAGE = getattr(
    settings,
    "AIAPP_MASTER_PAGE",
    "https://www.jpx.co.jp/markets/statistics-equities/misc/01.html",
)
MASTER_URL_OVERRIDE = getattr(settings, "AIAPP_MASTER_URL", None)  # 直リンク指定時

# ---- utils -------------------------------------------------------------------
def _ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)

def _absolute_url(page_url: str, href: str) -> str:
    if href.startswith("http"):
        return href
    from urllib.parse import urljoin
    return urljoin(page_url, href)

def _is_xlsx(data: bytes) -> bool:
    return data[:4] == b"PK\x03\x04"  # ZIP

def _is_xls(data: bytes) -> bool:
    return data[:8] == b"\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1"  # OLE2

def _looks_html(data: bytes) -> bool:
    head = data[:200].lower()
    return (b"<html" in head) or (b"<!doctype html" in head)

def _find_excel_url_from_page(page_url: str) -> str | None:
    resp = requests.get(page_url, timeout=30)
    resp.raise_for_status()
    html = resp.text
    m = re.search(r'href="([^"]+\.xlsx)"', html, re.IGNORECASE)
    if not m:
        m = re.search(r'href="([^"]+\.xls)"', html, re.IGNORECASE)
    if not m:
        return None
    return _absolute_url(page_url, m.group(1))

def _to_xlsx_bytes_from_xls(binary: bytes) -> bytes:
    """
    .xlsバイナリ → 一時ファイルで libreoffice 変換 → .xlsx バイナリを返す
    """
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

def _read_all_sheets(binary: bytes) -> List[pd.DataFrame]:
    """
    Excelバイナリから全シートを読み、DataFrameのリストで返す。
    列名の判定は後段の正規化で行う。
    """
    if _looks_html(binary):
        raise RuntimeError("Got HTML instead of Excel (login/redirect?)")

    # .xls → .xlsx に変換してから openpyxl で読む
    if _is_xls(binary):
        binary = _to_xlsx_bytes_from_xls(binary)

    # xlsx扱いで全シート取得
    with pd.ExcelFile(io.BytesIO(binary), engine="openpyxl") as xf:
        dfs = []
        for sheet in xf.sheet_names:
            try:
                df = xf.parse(sheet)
                if not df.empty:
                    dfs.append(df)
            except Exception:
                # シートによっては非表形式の注意書き等が混ざるのでスキップ
                continue
    return dfs

def _pick_col(cols, *keys):
    low = {str(c).lower(): c for c in cols}
    for k in keys:
        k = k.lower()
        for lc, orig in low.items():
            if k in lc:
                return orig
    return None

def _normalize_one(df: pd.DataFrame) -> pd.DataFrame | None:
    """
    1シートを code/name/sector33 に正規化（列が見つからない場合は None を返す）。
    """
    if df is None or df.empty:
        return None

    code_col = _pick_col(df.columns, "code", "コード", "証券コード")
    name_col = _pick_col(df.columns, "name", "銘柄")
    sect_col = _pick_col(df.columns, "33", "sector", "業種")

    if not all([code_col, name_col, sect_col]):
        return None  # このシートはスキップ

    out = df[[code_col, name_col, sect_col]].copy()
    out.columns = ["code", "name", "sector33"]

    # 証券コード（4〜5桁）抽出。ETF/REIT/外国株でも多くは4桁。
    out["code"] = out["code"].astype(str).str.strip().str.extract(r"(\d{4,5})")[0]
    out["name"] = out["name"].astype(str).str.strip()
    out["sector33"] = out["sector33"].astype(str).str.strip()

    out = out.dropna(subset=["code", "name"])
    out = out.drop_duplicates(subset=["code"])
    return out

def _normalize_all(dfs: List[pd.DataFrame]) -> pd.DataFrame:
    keep: List[pd.DataFrame] = []
    for df in dfs:
        norm = _normalize_one(df)
        if norm is not None and not norm.empty:
            keep.append(norm)
    if not keep:
        raise ValueError("No valid sheets: could not find code/name/sector33 in any sheet")
    merged = pd.concat(keep, axis=0, ignore_index=True)
    merged = merged.drop_duplicates(subset=["code"]).reset_index(drop=True)
    return merged

def _save_csv(df: pd.DataFrame) -> str:
    out_dir = os.path.join(DEFAULT_MEDIA, MASTER_DIR)
    _ensure_dir(out_dir)
    out_name = f"master_{dt.date.today():%Y%m%d}.csv"
    out_path = os.path.join(out_dir, out_name)
    df.to_csv(out_path, index=False, encoding="utf-8")
    return out_path

# ---- upsert optimized for SQLite --------------------------------------------
def _upsert_db(df: pd.DataFrame) -> int:
    """
    SQLiteロックを避けるための効率的なupsert:
      1) 既存コードを一括取得
      2) 新規は bulk_create(ignore_conflicts=True, batch_size=500)
      3) 既存はまとめて読み直し→値更新→ bulk_update(batch_size=500)
    さらに、WALモード＋busy_timeout を設定、transaction.atomic で1回にまとめる。
    """
    with connection.cursor() as cur:
        try:
            cur.execute("PRAGMA journal_mode=WAL;")
        except Exception:
            pass
        try:
            cur.execute("PRAGMA busy_timeout=5000;")
        except Exception:
            pass

    codes = df["code"].tolist()

    from typing import Dict
    with transaction.atomic():
        existing_codes = set(
            StockMaster.objects.filter(code__in=codes).values_list("code", flat=True)
        )

        # 新規作成
        new_rows = df[~df["code"].isin(existing_codes)]
        to_create = [
            StockMaster(code=row["code"], name=row["name"], sector33=row["sector33"])
            for _, row in new_rows.iterrows()
        ]
        created_count = 0
        if to_create:
            StockMaster.objects.bulk_create(to_create, ignore_conflicts=True, batch_size=500)
            created_count = len(to_create)

        # 既存更新
        upd_rows = df[df["code"].isin(existing_codes)]
        if not upd_rows.empty:
            inst_map: Dict[str, StockMaster] = {
                obj.code: obj for obj in StockMaster.objects.filter(code__in=upd_rows["code"].tolist())
            }
            for _, row in upd_rows.iterrows():
                obj = inst_map.get(row["code"])
                if not obj:
                    continue
                obj.name = row["name"]
                obj.sector33 = row["sector33"]
            StockMaster.objects.bulk_update(inst_map.values(), ["name", "sector33"], batch_size=500)

    return created_count

# ---- public ------------------------------------------------------------------
def refresh_master(source_url: str | None = None) -> int:
    """
    最新Excelを自動DL→全シートをマージ→正規化→CSV保存→DB反映。
    優先順位: 引数source_url > settings.AIAPP_MASTER_URL > settings.AIAPP_MASTER_PAGEスクレイプ
    戻り値: 新規insert件数
    """
    url_or_path = source_url or MASTER_URL_OVERRIDE

    # 1) 直指定（URL/ローカル） or 2) 一覧ページスクレイプ
    if url_or_path:
        if url_or_path.startswith("http"):
            resp = requests.get(url_or_path, timeout=30)
            resp.raise_for_status()
            binary = resp.content
        else:
            ext = os.path.splitext(url_or_path)[1].lower()
            if ext in (".xls", ".xlsx"):
                binary = open(url_or_path, "rb").read()
            else:
                # CSVを指定された場合は、そのまま正規化に回す
                for enc in ("utf-8", "cp932", "shift_jis", "utf-8-sig"):
                    try:
                        df = pd.read_csv(url_or_path, encoding=enc)
                        break
                    except Exception:
                        continue
                else:
                    raise RuntimeError(f"CSV read failed: {url_or_path}")
                merged = df.rename(columns={df.columns[0]:"code", df.columns[1]:"name", df.columns[2]:"sector33"})
                merged["code"] = merged["code"].astype(str).str.extract(r"(\d{4,5})")[0]
                merged = merged.dropna(subset=["code","name"]).drop_duplicates(subset=["code"])
                _save_csv(merged)
                return _upsert_db(merged)
    else:
        excel_url = _find_excel_url_from_page(MASTER_PAGE)
        if not excel_url:
            raise RuntimeError("Could not find JPX master excel link on page")
        resp = requests.get(excel_url, timeout=30)
        resp.raise_for_status()
        binary = resp.content

    # Excel全シートを読み→正規化・マージ
    dfs = _read_all_sheets(binary)
    merged = _normalize_all(dfs)

    # 保存＆DB反映
    _save_csv(merged)
    n_new = _upsert_db(merged)
    return n_new
