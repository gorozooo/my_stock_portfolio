"""
aiapp.services.fetch_master
JPX公式ページから最新の「東証上場銘柄一覧（Excel）」を自動取得し、
銘柄コード・銘柄名・33業種を正規化してCSV保存＆DBへ反映します。

仕様:
- まず JPXの一覧ページ（東証上場銘柄一覧）を取得し、当月Excelへのリンクを自動検出
- Excelをダウンロード → 必要3列を抽出 → code/name/sector33 に正規化
- CSVを MEDIA_ROOT/aiapp/master/master_YYYYMMDD.csv へ保存
- DBテーブル aiapp_stock_master を upsert

設定（settings.py、未指定でもOK）:
- AIAPP_MASTER_PAGE: 一覧ページURL（既定: JPXの「東証上場銘柄一覧」ページ）
- AIAPP_MASTER_URL : Excel直リンク（指定時はスクレイプをスキップし、このURLを直接DL）
- AIAPP_MASTER_DIR : MEDIA_ROOT 配下の保存先（既定: "aiapp/master"）
- MEDIA_ROOT       : 既定 "media"

依存:
    pip install requests pandas openpyxl
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

# --- 設定とデフォルト ----------------------------------------------------------
DEFAULT_MEDIA = getattr(settings, "MEDIA_ROOT", "media")
MASTER_DIR = getattr(settings, "AIAPP_MASTER_DIR", os.path.join("aiapp", "master"))

# 月次Excelへのリンクが貼られるJPX公式ページ（東証上場銘柄一覧）
# 例: https://www.jpx.co.jp/markets/statistics-equities/misc/01.html
MASTER_PAGE = getattr(
    settings,
    "AIAPP_MASTER_PAGE",
    "https://www.jpx.co.jp/markets/statistics-equities/misc/01.html",
)

# 直リンクを強制したい場合（テスト/固定運用）
MASTER_URL_OVERRIDE = getattr(settings, "AIAPP_MASTER_URL", None)


# --- 内部ユーティリティ --------------------------------------------------------
def _ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)

def _absolute_url(page_url: str, href: str) -> str:
    if href.startswith("http"):
        return href
    # JPXは相対パス（/markets/...）が多い
    from urllib.parse import urljoin
    return urljoin(page_url, href)

def _find_excel_url_from_page(page_url: str) -> str | None:
    """
    一覧ページHTMLから、当月のExcelリンク（.xls/.xlsx）を探して返す。
    JPXは '...-att/xxxxx.xls' のような添付リンクになっていることが多い。
    """
    resp = requests.get(page_url, timeout=20)
    resp.raise_for_status()
    html = resp.text

    # まず .xls/.xlsx を探す（日本語ページの "att" 付与パスが多い）
    # 例: href="/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xls"
    m = re.search(r'href="([^"]+\.(?:xls|xlsx))"', html, re.IGNORECASE)
    if not m:
        return None
    href = m.group(1)
    return _absolute_url(page_url, href)

def _read_excel_any(binary: bytes) -> pd.DataFrame:
    """
    ExcelバイナリからDataFrameを読む（シート名は先頭を想定）。
    """
    bio = io.BytesIO(binary)
    return pd.read_excel(bio, engine="openpyxl")

def _pick_col(cols, *keys):
    low = {c.lower(): c for c in cols}
    for k in keys:
        for lc, orig in low.items():
            if k in lc:
                return orig
    return None

def _normalize(df: pd.DataFrame) -> pd.DataFrame:
    """
    JPXのExcel列は時期により表記揺れあり。
    想定パターンから「コード/銘柄名/33業種」の列を抽出して正規化する。
    """
    code_col = _pick_col(df.columns, "code", "コード", "証券コード")
    name_col = _pick_col(df.columns, "name", "銘柄")
    sect_col = _pick_col(df.columns, "33", "sector", "業種")

    if not all([code_col, name_col, sect_col]):
        # デバッグしやすいよう列名を出す
        raise ValueError(f"master columns not found: {list(df.columns)}")

    out = df[[code_col, name_col, sect_col]].copy()
    out.columns = ["code", "name", "sector33"]
    # 証券コードは4〜5桁の数字のみ採用（ETF/ETN等も含めたい場合はここを緩める）
    out["code"] = (
        out["code"]
        .astype(str)
        .str.strip()
        .str.extract(r"(\d{4,5})")[0]
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


# --- 公開API -------------------------------------------------------------------
def refresh_master(source_url: str | None = None) -> int:
    """
    JPX公式ページから最新Excelを自動取得→正規化→CSV保存→DB反映。
    戻り値: 新規insert件数

    優先順位:
      1) 引数 source_url が指定されていればそれを使用（http/https or ローカルパス）
      2) settings.AIAPP_MASTER_URL が設定されていればそれを使用（直リンク）
      3) settings.AIAPP_MASTER_PAGE をスクレイピングしてExcelリンクを自動検出
    """
    url_or_path = source_url or MASTER_URL_OVERRIDE

    # 1) 直指定: URL or ローカルパス
    if url_or_path:
        if url_or_path.startswith("http"):
            resp = requests.get(url_or_path, timeout=30)
            resp.raise_for_status()
            df_raw = _read_excel_any(resp.content)
        else:
            # ローカルファイル: xls/xlsx/csv いずれも許容
            ext = os.path.splitext(url_or_path)[1].lower()
            if ext in (".xls", ".xlsx"):
                with open(url_or_path, "rb") as f:
                    df_raw = _read_excel_any(f.read())
            else:
                # CSV（エンコード自動トライ）
                for enc in ("utf-8", "cp932", "shift_jis", "utf-8-sig"):
                    try:
                        df_raw = pd.read_csv(url_or_path, encoding=enc)
                        break
                    except Exception:
                        continue
                else:
                    raise RuntimeError(f"CSV read failed: {url_or_path}")
    else:
        # 2) 一覧ページをスクレイピングしてExcel URLを検出
        excel_url = _find_excel_url_from_page(MASTER_PAGE)
        if not excel_url:
            raise RuntimeError("Could not find JPX master excel link on page")
        resp = requests.get(excel_url, timeout=30)
        resp.raise_for_status()
        df_raw = _read_excel_any(resp.content)

    # 正規化 → CSV保存 → DB反映
    df = _normalize(df_raw)
    csv_path = _save_csv(df)
    n_new = _upsert_db(df)

    # ログ用に返す insert件数。必要なら print(csv_path) で保存先も確認可。
    return n_new
