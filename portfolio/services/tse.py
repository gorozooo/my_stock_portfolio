# portfolio/services/tse.py
from __future__ import annotations
from typing import List, Tuple, Optional
import os
import re
import unicodedata
import time

import pandas as pd

# ================================================================
# 設定
# ================================================================
BASE_DIR = os.path.dirname(os.path.dirname(__file__))
TSE_JSON_PATH = os.environ.get("TSE_JSON_PATH", os.path.join(BASE_DIR, "data", "tse_list.json"))
TSE_CSV_PATH  = os.environ.get("TSE_CSV_PATH",  os.path.join(BASE_DIR, "data", "tse_list.csv"))

# 簡易キャッシュ
_cached_df: Optional[pd.DataFrame] = None
_cached_src: Optional[str] = None  # "json" / "csv"
_cached_mtime: float = 0.0


# ================================================================
# ユーティリティ
# ================================================================
def _clean(s: str) -> str:
    """Excel/CSV 由来の不可視文字を除去し、NFKC 正規化・空白整形。"""
    if not isinstance(s, str):
        return s
    s = unicodedata.normalize("NFKC", s)
    # zero width & BOM
    s = re.sub(r"[\u200B-\u200D\uFEFF]", "", s)
    # variation selectors
    s = re.sub(r"[\uFE00-\uFE0F]", "", s)
    # control chars + DEL
    s = re.sub(r"[\u0000-\u001F\u007F]", "", s)
    # Private Use Area
    s = re.sub(r"[\uE000-\uF8FF]", "", s)
    # 全角スペース → 半角
    s = s.replace("\u3000", " ")
    # 余計な空白を1つに
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def _pick_cols(df: pd.DataFrame) -> pd.DataFrame:
    """列名ゆらぎを吸収して code/name の2列に整える。"""
    cols = {c.lower(): c for c in df.columns}
    code = cols.get("code") or cols.get("ticker") or cols.get("symbol")
    name = cols.get("name") or cols.get("jp_name") or cols.get("company")
    if not (code and name):
        raise RuntimeError("tse list needs 'code' and 'name' columns")
    df = df[[code, name]].rename(columns={code: "code", name: "name"})
    return df


def _load_df_from_file(path: str, kind: str) -> pd.DataFrame:
    """kind='json' or 'csv'"""
    if kind == "json":
        df = pd.read_json(path, orient="records")
    else:
        df = pd.read_csv(path, encoding="utf-8-sig", dtype=str)
    df = _pick_cols(df)
    df["code"] = df["code"].astype(str).map(_clean).str.upper()
    df["name"] = df["name"].astype(str).map(_clean)
    # 同一コード重複を除去（先勝ち）
    df = df.dropna().drop_duplicates(subset=["code"])
    return df


def _source_mtime(path: str) -> float:
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


def _load_df() -> pd.DataFrame:
    """JSON 優先 → CSV フォールバック。変更があれば再読込。"""
    global _cached_df, _cached_src, _cached_mtime

    # どちらを使うか決定（JSON 優先）
    if os.path.isfile(TSE_JSON_PATH):
        use_path, src = TSE_JSON_PATH, "json"
    elif os.path.isfile(TSE_CSV_PATH):
        use_path, src = TSE_CSV_PATH, "csv"
    else:
        # 何もなければ空
        _cached_df, _cached_src, _cached_mtime = pd.DataFrame(columns=["code", "name"]), None, 0.0
        return _cached_df

    mtime = _source_mtime(use_path)
    if _cached_df is not None and _cached_src == src and _cached_mtime == mtime:
        return _cached_df

    df = _load_df_from_file(use_path, src)
    _cached_df, _cached_src, _cached_mtime = df, src, mtime
    return df

def lookup_name_jp(code: str) -> Optional[str]:
    df = _load_df()
    hit = df[df["code"].str.upper() == code.upper()]
    if not hit.empty:
        return hit.iloc[0]["name"]
    return None


# ================================================================
# 公開 API
# ================================================================
def search(q: str, limit: int = 8) -> List[Tuple[str, str]]:
    """
    サジェスト検索:
      - code 前方一致（'80' -> 8058 …）
      - name 部分一致（'三菱' / '銀行' など）
    戻り値: [(code, name), ...]  最大 limit 件
    """
    q = _clean(q or "")
    if not q:
        return []

    df = _load_df()
    if df.empty:
        return []

    # 優先1: コード前方一致（大小区別なし）
    q_upper = q.upper()
    hits_code = df[df["code"].str.startswith(q_upper, na=False)]

    # 優先2: 名前部分一致（大文字/小文字は無視、日本語もOK）
    # pandas の contains でエスケープ
    hits_name = df[df["name"].str.contains(re.escape(q), case=False, na=False)]

    # 連結して重複コードを除去（コード一致を優先した順序）
    merged = pd.concat([hits_code, hits_name], ignore_index=True)
    merged = merged.drop_duplicates(subset=["code"]).head(limit)

    return [(row["code"], row["name"]) for _, row in merged.iterrows()]