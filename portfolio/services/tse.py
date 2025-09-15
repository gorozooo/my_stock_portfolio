from __future__ import annotations
from typing import List, Tuple, Optional, Dict
import os
import re
import unicodedata
import pandas as pd

BASE_DIR = os.path.dirname(os.path.dirname(__file__))

# 環境変数で上書き可
_TSE_JSON_PATH = os.environ.get("TSE_JSON_PATH", os.path.join(BASE_DIR, "data", "tse_list.json"))
_TSE_CSV_PATH  = os.environ.get("TSE_CSV_PATH",  os.path.join(BASE_DIR, "data", "tse_list.csv"))
_TSE_ALWAYS_RELOAD = os.environ.get("TSE_CSV_ALWAYS_RELOAD", "0") == "1"
_TSE_DEBUG = os.environ.get("TSE_DEBUG", "0") == "1"

# モジュール内キャッシュ
_TSE_MAP: Dict[str, str] = {}
_TSE_MTIME: Tuple[float, float] = (0.0, 0.0)  # (json_mtime, csv_mtime)


def _d(msg: str) -> None:
    if _TSE_DEBUG:
        print(f"[TSE] {msg}")


def _clean(s: str) -> str:
    if not isinstance(s, str):
        return s
    s = unicodedata.normalize("NFKC", s)
    s = re.sub(r"[\u200B-\u200D\uFEFF]", "", s)      # zero width & BOM
    s = re.sub(r"[\uFE00-\uFE0F]", "", s)            # variation selectors
    s = re.sub(r"[\u0000-\u001F\u007F]", "", s)      # control chars + DEL
    s = re.sub(r"[\uE000-\uF8FF]", "", s)            # PUA (BMP)
    s = s.replace("\u3000", " ")
    return re.sub(r"\s+", " ", s).strip()


def _load_df() -> pd.DataFrame:
    """JSON優先、無ければCSVを読む。列名は code/name を想定（柔軟に解決）。"""
    if os.path.isfile(_TSE_JSON_PATH):
        df = pd.read_json(_TSE_JSON_PATH, orient="records")
        cols = {c.lower(): c for c in df.columns}
        code = cols.get("code") or cols.get("ticker") or cols.get("symbol")
        name = cols.get("name") or cols.get("jp_name") or cols.get("company")
        if code and name:
            df = df[[code, name]].rename(columns={code: "code", name: "name"})
        else:
            raise RuntimeError("tse_list.json needs 'code' and 'name'")
        return df

    if os.path.isfile(_TSE_CSV_PATH):
        df = pd.read_csv(_TSE_CSV_PATH, encoding="utf-8-sig", dtype=str)
        df = df.rename(columns={c: c.lower() for c in df.columns})
        if not {"code", "name"}.issubset(df.columns):
            raise RuntimeError("tse_list.csv needs 'code' and 'name'")
        return df[["code", "name"]]

    # 何も無ければ空
    return pd.DataFrame(columns=["code", "name"])


def _refresh_cache_if_needed() -> None:
    """ファイルの更新時刻が変わっていれば再読込。"""
    global _TSE_MAP, _TSE_MTIME
    json_m = os.path.getmtime(_TSE_JSON_PATH) if os.path.isfile(_TSE_JSON_PATH) else 0.0
    csv_m  = os.path.getmtime(_TSE_CSV_PATH)  if os.path.isfile(_TSE_CSV_PATH)  else 0.0

    if not _TSE_ALWAYS_RELOAD and _TSE_MAP and _TSE_MTIME == (json_m, csv_m):
        return

    df = _load_df()
    if df.empty:
        _TSE_MAP = {}
        _TSE_MTIME = (json_m, csv_m)
        _d("loaded empty list")
        return

    df["code"] = df["code"].astype(str).map(_clean).str.upper()
    df["name"] = df["name"].astype(str).map(_clean)

    _TSE_MAP = {row["code"]: row["name"] for _, row in df.iterrows() if row["code"] and row["name"]}
    _TSE_MTIME = (json_m, csv_m)
    _d(f"loaded {len(_TSE_MAP)} rows")


def lookup_name_jp(code_or_ticker: str) -> Optional[str]:
    """
    '8058' / '8058.T' / '167A' / '167A.T' などから **日本語名** を返す。
    無ければ None。
    """
    _refresh_cache_if_needed()
    if not _TSE_MAP:
        return None
    if not code_or_ticker:
        return None
    head = code_or_ticker.upper().split(".", 1)[0]  # ドット前をキーに
    name = _TSE_MAP.get(head)
    if _TSE_DEBUG:
        _d(f"lookup {head} -> {repr(name)}")
    return name


def search(q: str, limit: int = 8) -> List[Tuple[str, str]]:
    """
    サジェスト用：code 前方一致 or name 部分一致
    戻り値: [(code, name), ...]
    """
    q = _clean(q or "")
    if not q:
        return []

    _refresh_cache_if_needed()
    if not _TSE_MAP:
        return []

    q_upper = q.upper()

    # ✅ ここがポイント: list of tuple から DataFrame を作る
    df = pd.DataFrame(
        [(c, n) for c, n in _TSE_MAP.items()],
        columns=["code", "name"]
    )

    hits = df[
        df["code"].str.startswith(q_upper) |
        df["name"].str.contains(re.escape(q), case=False, na=False)
    ].head(limit)

    return [(row["code"], row["name"]) for _, row in hits.iterrows()]