"""
[FILE] autotrade/views_utils.py
[PATH] <project_root>/autotrade/views_utils.py

このファイルは何？
- autotrade の views を分割した後も、共通で使う小さな関数（ユーティリティ）を集めたファイルです。
- JSONField の intキー→strキー問題を吸収する helper などをここに置きます。
"""

from __future__ import annotations

from typing import Any, Dict, Optional


def get_nested_dict(d: dict, *keys, default=None):
    """
    dictをネストで辿る。
    - JSONField経由で intキーが str になるケースも吸収する。
    """
    cur = d
    for k in keys:
        if not isinstance(cur, dict):
            return default
        if k in cur:
            cur = cur.get(k)
            continue
        ks = str(k)
        if ks in cur:
            cur = cur.get(ks)
            continue
        return default
    return cur if cur is not None else default


def safe_int(v: Any, default: int = 0) -> int:
    try:
        if v is None:
            return int(default)
        return int(v)
    except Exception:
        try:
            return int(float(str(v).strip()))
        except Exception:
            return int(default)


def safe_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None:
            return float(default)
        return float(v)
    except Exception:
        try:
            return float(str(v).strip())
        except Exception:
            return float(default)


def delta_float(a: Optional[float], b: Optional[float]) -> Optional[float]:
    """
    a - b を返す。片方NoneならNone。
    """
    if a is None or b is None:
        return None
    try:
        return float(a) - float(b)
    except Exception:
        return None


def delta_int(a: Any, b: Any) -> Optional[int]:
    if a is None or b is None:
        return None
    try:
        return int(a) - int(b)
    except Exception:
        return None