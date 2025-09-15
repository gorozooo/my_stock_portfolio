# portfolio/services/tse.py
from __future__ import annotations
from typing import List, Tuple
import os
import re
import unicodedata
import pandas as pd

BASE_DIR = os.path.dirname(os.path.dirname(__file__))
_TSE_JSON_PATH = os.environ.get("TSE_JSON_PATH", os.path.join(BASE_DIR, "data", "tse_list.json"))
_TSE_CSV_PATH  = os.environ.get("TSE_CSV_PATH",  os.path.join(BASE_DIR, "data", "tse_list.csv"))

def _clean(s: str) -> str:
    if not isinstance(s, str):
        return s
    s = unicodedata.normalize("NFKC", s)
    s = re.sub(r"[\u200B-\u200D\uFEFF]", "", s)      # zero width & BOM
    s = re.sub(r"[\uFE00-\uFE0F]", "", s)            # variation selectors
    s = re.sub(r"[\u0000-\u001F\u007F]", "", s)      # control chars + DEL
    s = re.sub(r"[\uE000-\uF8FF]", "", s)            # PUA
    s = s.replace("\u3000", " ")
    return re.sub(r"\s+", " ", s).strip()

def _load_df() -> pd.DataFrame:
    # JSON優先、なければCSV
    if os.path.isfile(_TSE_JSON_PATH):
        df = pd.read_json(_TSE_JSON_PATH, orient="records")
        cols = {c.lower(): c for c in df.columns}
        code = cols.get("code") or cols.get("ticker") or cols.get("symbol")
        name = cols.get("name") or cols.get("jp_name") or cols.get("company")
        if code and name:
            df = df[[code, name]].rename(columns={code: "code", name: "name"})
        else:
            raise RuntimeError("tse_list.json needs 'code' and 'name'")
    elif os.path.isfile(_TSE_CSV_PATH):
        df = pd.read_csv(_TSE_CSV_PATH, encoding="utf-8-sig", dtype=str)
        df = df.rename(columns={c: c.lower() for c in df.columns})
        if not {"code", "name"}.issubset(df.columns):
            raise RuntimeError("tse_list.csv needs 'code' and 'name'")
        df = df[["code", "name"]]
    else:
        # 何もなければ空
        return pd.DataFrame(columns=["code", "name"])

    df["code"] = df["code"].astype(str).map(_clean).str.upper()
    df["name"] = df["name"].astype(str).map(_clean)
    return df.dropna().drop_duplicates(subset=["code"])

def search(q: str, limit: int = 8) -> List[Tuple[str, str]]:
    """
    q：数字/英字 or 日本語の一部
    - code 前方一致（'80' -> 8058 …）
    - name 部分一致（'三菱' / '銀行' など）
    戻り値: [(code, name), ...]
    """
    q = _clean(q or "")
    if not q:
        return []
    df = _load_df()
    if df.empty:
        return []

    q_upper = q.upper()
    hits = df[
        df["code"].str.startswith(q_upper) |
        df["name"].str.contains(re.escape(q), case=False, na=False)
    ].head(limit)

    return [(row["code"], row["name"]) for _, row in hits.iterrows()]