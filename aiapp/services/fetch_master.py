"""
aiapp.services.fetch_master
JPX公式ページから最新の「東証上場銘柄一覧（Excel）」を自動取得し、
銘柄コード・銘柄名・33業種を正規化してCSV保存＆DBへ反映します。

対応:
- .xlsx と .xls の両方に対応（バイナリをヘッダシグネチャで判別）
- 取得先は settings で上書き可（直リンク or 一覧ページ）
- 直リンクがHTMLにリダイレクトされた場合のガードあり

依存:
    pip install requests pandas openpyxl "xlrd==1.2.0"
"""

from __future__ import annotations
import os
import re
import io
import datetime as dt
import pandas as pd
import requests
from django.conf import settings
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
    # ZIP シグネチャ "PK\x03\x04"
    return data[:4] == b"PK\x03\x04"

def _is_xls(data: bytes) -> bool:
    # OLE2 シグネチャ D0 CF 11 E0
    return data[:8] == b"\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1"

def _looks_html(data: bytes) -> bool:
    head = data[:200].lower()
    return (b"<html" in head) or (b"<!doctype html" in head)

def _find_excel_url_from_page(page_url: str) -> str | None:
    """
    JPXの一覧ページから .xls/.xlsx のリンクを1つ検出。
    """
    resp = requests.get(page_url, timeout=30)
    resp.raise_for_status()
    html = resp.text
    # まず .xlsx を優先、それが無ければ .xls
    m = re.search(r'href="([^"]+\.xlsx)"', html, re.IGNORECASE)
    if not m:
        m = re.search(r'href="([^"]+\.xls)"', html, re.IGNORECASE)
    if not m:
        return None
    return _absolute_url(page_url, m.group(1))

def _read_excel_bytes(binary: bytes) -> pd.DataFrame:
    """
    バイナリから DataFrame を読む。.xlsx/.xls 自動判定。
    xlrd は使わず、一時ファイル経由で openpyxl が開ける形に変換する。
    """
    if _looks_html(binary):
        raise RuntimeError("Got HTML instead of Excel (login/redirect?)")

    # まず .xlsx/.xls 判定
    if _is_xlsx(binary):
        return pd.read_excel(io.BytesIO(binary), engine="openpyxl")

    if _is_xls(binary):
        import tempfile
        import subprocess, os
        from pathlib import Path
        tmp_in = Path(tempfile.mkstemp(suffix=".xls")[1])
        tmp_out = tmp_in.with_suffix(".xlsx")
        tmp_in.write_bytes(binary)
        try:
            # libreoffice / unoconv などを使って変換（Linux標準）
            subprocess.run(
                ["libreoffice", "--headless", "--convert-to", "xlsx", str(tmp_in), "--outdir", str(tmp_in.parent)],
                check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            df = pd.read_excel(tmp_out, engine="openpyxl")
        except Exception as e:
            raise RuntimeError(f"xls→xlsx変換に失敗: {e}")
        finally:
            for p in (tmp_in, tmp_out):
                try:
                    os.remove(p)
                except OSError:
                    pass
        return df

    # どちらでもなければ、openpyxlで強制読込
    return pd.read_excel(io.BytesIO(binary), engine="openpyxl")


def _pick_col(cols, *keys):
    low = {str(c).lower(): c for c in cols}
    for k in keys:
        k = k.lower()
        for lc, orig in low.items():
            if k in lc:
                return orig
    return None

def _normalize(df: pd.DataFrame) -> pd.DataFrame:
    """
    列名の揺れを許容して code/name/sector33 を抽出・正規化。
    """
    code_col = _pick_col(df.columns, "code", "コード", "証券コード")
    name_col = _pick_col(df.columns, "name", "銘柄")
    sect_col = _pick_col(df.columns, "33", "sector", "業種")
    if not all([code_col, name_col, sect_col]):
        raise ValueError(f"master columns not found: {list(df.columns)}")

    out = df[[code_col, name_col, sect_col]].copy()
    out.columns = ["code", "name", "sector33"]

    # 証券コード（4〜5桁）抽出。空白・注記の混入に強く
    out["code"] = (
        out["code"].astype(str).str.strip().str.extract(r"(\d{4,5})")[0]
    )
    out["name"] = out["name"].astype(str).str.strip()
    out["sector33"] = out["sector33"].astype(str).str.strip()

    out = out.dropna(subset=["code", "name"])
    out = out.drop_duplicates(subset=["code"])
    return out

def _save_csv(df: pd.DataFrame) -> str:
    out_dir = os.path.join(DEFAULT_MEDIA, MASTER_DIR)
    _ensure_dir(out_dir)
    out_name = f"master_{dt.date.today():%Y%m%d}.csv"
    out_path = os.path.join(out_dir, out_name)
    df.to_csv(out_path, index=False, encoding="utf-8")
    return out_path

def _upsert_db(df: pd.DataFrame) -> int:
    n_new = 0
    for _, row in df.iterrows():
        _, created = StockMaster.objects.update_or_create(
            code=row["code"],
            defaults={"name": row["name"], "sector33": row["sector33"]},
        )
        if created:
            n_new += 1
    return n_new

# ---- public ------------------------------------------------------------------
def refresh_master(source_url: str | None = None) -> int:
    """
    最新Excelを自動DL→正規化→CSV保存→DB反映。
    優先順位: 引数source_url > settings.AIAPP_MASTER_URL > settings.AIAPP_MASTER_PAGEスクレイプ
    戻り値: 新規insert件数
    """
    url_or_path = source_url or MASTER_URL_OVERRIDE

    if url_or_path:
        # 直指定（URL or ローカル）
        if url_or_path.startswith("http"):
            resp = requests.get(url_or_path, timeout=30)
            resp.raise_for_status()
            df_raw = _read_excel_bytes(resp.content)
        else:
            # ローカル: xls/xlsx/csv 何でもOK
            ext = os.path.splitext(url_or_path)[1].lower()
            if ext in (".xls", ".xlsx"):
                with open(url_or_path, "rb") as f:
                    df_raw = _read_excel_bytes(f.read())
            else:
                # CSVはエンコード自動トライ
                for enc in ("utf-8", "cp932", "shift_jis", "utf-8-sig"):
                    try:
                        df_raw = pd.read_csv(url_or_path, encoding=enc)
                        break
                    except Exception:
                        continue
                else:
                    raise RuntimeError(f"CSV read failed: {url_or_path}")
    else:
        # 一覧ページを読んで .xlsx/.xls の添付URLを検出
        excel_url = _find_excel_url_from_page(MASTER_PAGE)
        if not excel_url:
            raise RuntimeError("Could not find JPX master excel link on page")
        resp = requests.get(excel_url, timeout=30)
        resp.raise_for_status()
        df_raw = _read_excel_bytes(resp.content)

    df = _normalize(df_raw)
    _save_csv(df)
    n_new = _upsert_db(df)
    return n_new
