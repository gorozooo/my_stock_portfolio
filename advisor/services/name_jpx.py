from __future__ import annotations
import os, json
from typing import Dict, Any, Tuple, Optional, Union
from django.conf import settings

_CACHE: Dict[str, Union[str, Dict[str, Any]]] = {}
_LOADED = False

def _load() -> None:
    global _LOADED, _CACHE
    if _LOADED: return
    base = getattr(settings, "BASE_DIR", os.getcwd())
    path = os.path.join(base, "data", "tse_list.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f)
            if isinstance(d, dict):
                _CACHE = d
    except Exception:
        _CACHE = {}
    _LOADED = True

def jpx_lookup(ticker: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """-> (jp_name, sector, market) すべて無ければ None"""
    _load()
    t = str(ticker).upper().strip()
    if t.endswith(".T"): t = t[:-2]
    v = _CACHE.get(t)
    if v is None: return None, None, None
    if isinstance(v, str): return (v.strip() or None), None, None
    name = (str(v.get("name") or "").strip() or None)
    sector = (str(v.get("sector") or "").strip() or None) if "sector" in v else None
    market = (str(v.get("market") or "").strip() or None) if "market" in v else None
    return name, sector, market

def normalize_payload_names(payload: Dict[str, Any]) -> Dict[str, Any]:
    """highlights[].name を必ず str 和名に統一（なければ元の値→最終的に str）。"""
    _load()
    hs = payload.get("highlights") or []
    for h in hs:
        t = str(h.get("ticker") or "").upper()
        jp, sector, market = jpx_lookup(t)
        name = jp or h.get("name") or t
        # dictやNoneでも最終的に文字列へ
        if isinstance(name, dict): name = name.get("name") or ""
        h["name"] = str(name)
        # おまけ: sector/marketを meta に載せる（UIが使いたい時用）
        meta = dict(h.get("meta") or {})
        if jp: meta.setdefault("jpx_name", jp)
        if sector: meta["sector"] = sector
        if market: meta["market"] = market
        if meta: h["meta"] = meta
    return payload