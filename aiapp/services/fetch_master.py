"""
aiapp.services.fetch_master
JPX公式の上場銘柄リスト（銘柄名・コード・33業種）を取り込み、DBとCSVに保存。

設定（settings.pyで任意指定、未定義でも動作）:
- AIAPP_MASTER_URL: JPX公式CSVのURL or ローカルファイルパス
- AIAPP_MEDIA_ROOT: 既存の MEDIA_ROOT を使用（未定義なら 'media' を使用）
- AIAPP_MASTER_DIR: 'aiapp/master'（MEDIA_ROOT配下）
保存先: {MEDIA_ROOT}/aiapp/master/master_YYYYMMDD.csv

注: 実際のJPX配布フォーマットは時期により列名が異なるため、
    下の _normalize() で柔軟に吸収しています。
"""

from __future__ import annotations
import os
import io
import datetime as dt
import pandas as pd
import requests
from django.conf import settings
from aiapp.models import StockMaster

DEFAULT_MEDIA = getattr(settings, "MEDIA_ROOT", "media")
MASTER_DIR = getattr(settings, "AIAPP_MASTER_DIR", os.path.join("aiapp", "master"))
MASTER_URL = getattr(settings, "AIAPP_MASTER_URL", None)  # URL or local path（未設定可）

def _ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)

def _normalize(df: pd.DataFrame) -> pd.DataFrame:
    # 代表的な列名の候補：'Code','コード','証券コード' / 'Name','銘柄名' / '33業種','Sector'
    cols = {c.lower(): c for c in df.columns}
    code_col = next((cols[k] for k in cols if "code" in k or "コード" in k), None)
    name_col = next((cols[k] for k in cols if "name" in k or "銘柄" in k), None)
    sector_col = next((cols[k] for k in cols if "33" in k or "sector" in k or "業種" in k), None)
    if not all([code_col, name_col, sector_col]):
        raise ValueError(f"master columns not found: {df.columns.tolist()}")

    out = df[[code_col, name_col, sector_col]].copy()
    out.columns = ["code", "name", "sector33"]
    # JPX形式だと 4桁 or 5桁 + 市場記号なし想定。内部は文字列で保持。
    out["code"] = out["code"].astype(str).str.strip()
    out["name"] = out["name"].astype(str).str.strip()
    out["sector33"] = out["sector33"].astype(str).str.strip()
    out = out.dropna(subset=["code", "name"])
    out = out[out["code"].str.match(r"^\d{4,5}$")]  # 4〜5桁の数字のみ
    out = out.drop_duplicates(subset=["code"])
    return out

def refresh_master(source_url: str | None = None) -> int:
    url_or_path = source_url or MASTER_URL
    if not url_or_path:
        # URL未設定の場合は既存を維持（0件）
        return 0

    # 読み込み
    if url_or_path.startswith("http"):
        resp = requests.get(url_or_path, timeout=20)
        resp.raise_for_status()
        raw = io.BytesIO(resp.content)
        df = pd.read_csv(raw)
    else:
        df = pd.read_csv(url_or_path)

    df = _normalize(df)

    # 保存先
    media_root = DEFAULT_MEDIA
    out_dir = os.path.join(media_root, MASTER_DIR)
    _ensure_dir(out_dir)
    out_name = f"master_{dt.date.today():%Y%m%d}.csv"
    out_path = os.path.join(out_dir, out_name)
    df.to_csv(out_path, index=False, encoding="utf-8")

    # DB反映（upsert相当）
    n = 0
    for _, row in df.iterrows():
        _, created = StockMaster.objects.update_or_create(
            code=row["code"],
            defaults={"name": row["name"], "sector33": row["sector33"]},
        )
        if created:
            n += 1
    return n
