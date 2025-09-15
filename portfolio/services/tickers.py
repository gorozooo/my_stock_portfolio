# portfolio/services/tickers.py
from __future__ import annotations
from pathlib import Path
from typing import Optional, Dict

import pandas as pd
from django.conf import settings

# キャッシュ（プロセス内）
_CACHE: Optional[Dict[str, str]] = None

def _base_dir() -> Path:
    try:
        return Path(settings.BASE_DIR)  # type: ignore[attr-defined]
    except Exception:
        return Path(__file__).resolve().parents[3]  # project root

def _csv_path() -> Path:
    path_in_settings = getattr(settings, "TSE_LIST_CSV_PATH", None)
    if path_in_settings:
        return Path(path_in_settings)
    return _base_dir() / "data" / "tse_list.csv"

def _load_csv_into_cache() -> Dict[str, str]:
    global _CACHE
    path = _csv_path()
    if not path.exists():
        _CACHE = {}
        return _CACHE
    df = pd.read_csv(path, dtype={"code": str, "name": str})
    df["code"] = df["code"].astype(str).str.strip()
    df["name"] = df["name"].astype(str).str.strip()
    _CACHE = {row["code"]: row["name"] for _, row in df.iterrows() if row["code"] and row["name"]}
    return _CACHE

def _ensure_cache() -> Dict[str, str]:
    global _CACHE
    if _CACHE is None:
        return _load_csv_into_cache()
    return _CACHE

def _normalize_to_code(value: str) -> Optional[str]:
    """
    '7203' / '7203.T' のどちらでも 4桁コードに正規化して返す。
    それ以外は None。
    """
    if not value:
        return None
    t = value.strip().upper()
    if "." in t:
        t = t.split(".", 1)[0]
    return t if (len(t) == 4 and t.isdigit()) else None

def resolve_name(ticker_or_code: str) -> Optional[str]:
    """
    CSV を元に銘柄名を返す（見つからなければ None）
    """
    code = _normalize_to_code(ticker_or_code)
    if not code:
        return None
    m = _ensure_cache()
    return m.get(code)