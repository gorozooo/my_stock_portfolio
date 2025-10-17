# portfolio/services/sector_map.py
# -*- coding: utf-8 -*-
from __future__ import annotations
import re
from typing import Dict, List

# JPX33（代表名）
JPX33: List[str] = [
    "水産・農林業", "鉱業", "建設業", "食料品", "繊維製品", "パルプ・紙", "化学",
    "医薬品", "石油・石炭製品", "ゴム製品", "ガラス・土石製品", "鉄鋼", "非鉄金属",
    "金属製品", "機械", "電気機器", "輸送用機器", "精密機器", "その他製品",
    "電気・ガス業", "陸運業", "海運業", "空運業", "倉庫・運輸関連業",
    "情報・通信業", "卸売業", "小売業", "銀行業", "証券、商品先物取引業",
    "保険業", "その他金融業", "不動産業", "サービス業",
]

# 別表記の吸収（最低限）
ALIASES: Dict[str, str] = {
    "情報・通信": "情報・通信業", "情報通信": "情報・通信業",
    "電気機器": "電気機器",
    "サービス": "サービス業",
    "証券": "証券、商品先物取引業",
    "REIT": "不動産業",
}

def _zen2han(s: str) -> str:
    try:
        import unicodedata as ud
        return "".join(ud.normalize("NFKC", ch) for ch in s)
    except Exception:
        return s

def normalize_sector(name: str) -> str:
    s = (name or "").strip()
    if not s:
        return "未分類"
    s = _zen2han(s)
    s = s.replace("　", " ")
    s = s.replace("通信業", "通信").replace("情報通信", "情報・通信")
    if s in ALIASES:
        s = ALIASES[s]
    # 完全一致
    if s in JPX33:
        return s
    # ゆるマッチ（業/業種のゆらぎなど）
    s2 = re.sub(r"(業|業種)$", "", s)
    for cand in JPX33:
        c2 = re.sub(r"(業|業種)$", "", cand)
        if s2 == c2:
            return cand
    return s  # 最後まで合わなければ元の文字列（呼び元で「未分類」扱い可）

def map_pf_sectors(sectors: List[Dict]) -> List[Dict]:
    """
    PFのセクター配列（[{sector, mv, ...}]）を正規化して返す。
    sector を JPX33に寄せ、未分類は '未分類' に統一。
    """
    out = []
    for s in (sectors or []):
        sec = normalize_sector(s.get("sector") or "")
        if sec not in JPX33 and sec != "未分類":
            sec = sec  # 非JPX33でもそのまま残す（集計側で未分類扱い可）
        out.append({**s, "sector": sec or "未分類"})
    return out