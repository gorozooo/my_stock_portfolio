# -*- coding: utf-8 -*-
"""
ユニバース（銘柄一覧）の読み込みと、StockMaster による銘柄名/業種の補完。

- all_jpx は StockMaster から全件
- nk225 は aiapp/data/universe/nk225.txt
- その他は aiapp/data/universe/<name>.txt
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Tuple

# オプション扱い（無くても動く）
try:
    from aiapp.models import StockMaster
except Exception:  # pragma: no cover
    StockMaster = None  # type: ignore

from .schema import PickItem


def load_universe_from_txt(name: str) -> List[str]:
    base = Path("aiapp/data/universe")
    filename = name if name.endswith(".txt") else f"{name}.txt"
    txt = base / filename
    if not txt.exists():
        print(f"[picks_build] universe file not found: {txt}")
        return []
    codes: List[str] = []
    for line in txt.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        codes.append(line.split(",")[0].strip())
    return codes


def load_universe_all_jpx() -> List[str]:
    if StockMaster is None:
        print("[picks_build] StockMaster not available; ALL-JPX empty")
        return []
    try:
        qs = StockMaster.objects.values_list("code", flat=True).order_by("code")
        codes = [str(c).strip() for c in qs if c]
        print(f"[picks_build] ALL-JPX from StockMaster: {len(codes)} codes")
        return codes
    except Exception as e:
        print(f"[picks_build] ALL-JPX load error: {e}")
        return []


def load_universe(name: str) -> List[str]:
    key = (name or "").strip().lower()

    if key in ("all_jpx", "all", "jpx_all"):
        codes = load_universe_all_jpx()
        if codes:
            return codes
        print("[picks_build] ALL-JPX fallback to txt")
        return load_universe_from_txt("all_jpx")

    if key in ("nk225", "nikkei225", "nikkei_225"):
        return load_universe_from_txt("nk225")

    return load_universe_from_txt(key)


def enrich_meta(items: List[PickItem]) -> None:
    if not items or StockMaster is None:
        return
    codes = [it.code for it in items if it and it.code]
    if not codes:
        return
    try:
        qs = StockMaster.objects.filter(code__in=codes).values("code", "name", "sector_name")
        meta: Dict[str, Tuple[str, str]] = {
            str(r["code"]): (r.get("name") or "", r.get("sector_name") or "")
            for r in qs
        }
        for it in items:
            if it.code in meta:
                nm, sec = meta[it.code]
                if not it.name:
                    it.name = nm or None
                if not it.sector_display:
                    it.sector_display = sec or None
    except Exception:
        pass