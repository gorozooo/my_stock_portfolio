# -*- coding: utf-8 -*-
from __future__ import annotations
import re
from typing import Dict, List, Any

# =========================================
# JPX 33 業種の“正規名”
# =========================================
JPX33 = [
    "水産・農林業",
    "鉱業",
    "建設業",
    "食料品",
    "繊維製品",
    "パルプ・紙",
    "化学",
    "医薬品",
    "石油・石炭製品",
    "ゴム製品",
    "ガラス・土石製品",
    "鉄鋼",
    "非鉄金属",
    "金属製品",
    "機械",
    "電気機器",
    "輸送用機器",
    "精密機器",
    "その他製品",
    "電気・ガス業",
    "陸運業",
    "海運業",
    "空運業",
    "倉庫・運輸関連業",
    "情報・通信業",
    "卸売業",
    "小売業",
    "銀行業",
    "証券、商品先物取引業",
    "保険業",
    "その他金融業",
    "不動産業",
    "サービス業",
]

# =========================================
# 正規化の基本方針
# - 全角・半角空白/記号の除去、末尾「業」や「関連」などの削り
# - よくある短縮/別表記/英語を吸収
# - 未判定は "未分類"
# =========================================

# ひらがな・カタカナ・英語・省略、日本語のハイフン抜き等を吸収
ALIAS_TABLE: Dict[str, str] = {
    # 大分類：製造業系
    "情報通信": "情報・通信業",
    "情報・通信": "情報・通信業",
    "ict": "情報・通信業",
    "it": "情報・通信業",
    "ソフトウェア": "情報・通信業",
    "メディア": "情報・通信業",

    "電機": "電気機器",
    "エレクトロニクス": "電気機器",
    "electronics": "電気機器",

    "機械": "機械",
    "machinery": "機械",

    "輸送用機器": "輸送用機器",
    "自動車": "輸送用機器",
    "auto": "輸送用機器",
    "automobile": "輸送用機器",

    "精密": "精密機器",
    "医療機器": "精密機器",

    "その他製品": "その他製品",

    "化学": "化学",
    "materials": "化学",

    "医薬": "医薬品",
    "pharma": "医薬品",
    "pharmaceutical": "医薬品",

    "石油石炭": "石油・石炭製品",
    "石油": "石油・石炭製品",
    "エネルギー": "石油・石炭製品",
    "energy": "石油・石炭製品",

    "ゴム": "ゴム製品",

    "ガラス土石": "ガラス・土石製品",
    "セメント": "ガラス・土石製品",

    "鉄鋼": "鉄鋼",
    "スチール": "鉄鋼",
    "steel": "鉄鋼",

    "非鉄": "非鉄金属",
    "アルミ": "非鉄金属",
    "銅": "非鉄金属",
    "nonferrous": "非鉄金属",

    "金属製品": "金属製品",

    "繊維": "繊維製品",
    "textile": "繊維製品",

    "紙": "パルプ・紙",
    "紙パルプ": "パルプ・紙",

    # 大分類：インフラ
    "電力ガス": "電気・ガス業",
    "電力": "電気・ガス業",
    "ガス": "電気・ガス業",
    "utilities": "電気・ガス業",

    # 大分類：運輸・物流
    "陸運": "陸運業",
    "鉄道": "陸運業",
    "トラック": "陸運業",

    "海運": "海運業",
    "shipping": "海運業",

    "空運": "空運業",
    "航空": "空運業",
    "airline": "空運業",

    "倉庫運輸": "倉庫・運輸関連業",
    "物流": "倉庫・運輸関連業",
    "logistics": "倉庫・運輸関連業",

    # 大分類：流通
    "卸売": "卸売業",
    "小売": "小売業",
    "retail": "小売業",
    "consumer": "小売業",

    # 大分類：金融
    "銀行": "銀行業",
    "bank": "銀行業",

    "証券": "証券、商品先物取引業",
    "ブローカー": "証券、商品先物取引業",
    "broker": "証券、商品先物取引業",

    "保険": "保険業",
    "insurance": "保険業",

    "ノンバンク": "その他金融業",
    "消費者金融": "その他金融業",
    "リース": "その他金融業",
    "クレジット": "その他金融業",
    "fintech": "その他金融業",

    # 大分類：不動産
    "不動産": "不動産業",
    "reits": "不動産業",
    "reit": "不動産業",
    "realestate": "不動産業",

    # 大分類：一次産業・資源
    "水産農林": "水産・農林業",
    "水産": "水産・農林業",
    "農林": "水産・農林業",
    "agri": "水産・農林業",

    "鉱業": "鉱業",
    "mining": "鉱業",

    # 大分類：建設・サービス
    "建設": "建設業",
    "ゼネコン": "建設業",
    "construction": "建設業",

    "サービス": "サービス業",
    "services": "サービス業",
}

# 句読点や記号（中黒・ハイフン・スペース）を除去して比較
_PUNCT_RE = re.compile(r"[・\-\s_/　、,\.]+")

def _canon(s: str) -> str:
    if not s:
        return ""
    s = s.strip()
    s = s.replace("業", "")  # “業”は落としてマッチしやすく
    s = _PUNCT_RE.sub("", s)
    s = s.lower()
    return s

# 逆引き辞書（正規名→正規名、別名→正規名）
CANON_TO_SECTOR: Dict[str, str] = {}
for sec in JPX33:
    CANON_TO_SECTOR[_canon(sec)] = sec
for alias, tgt in ALIAS_TABLE.items():
    CANON_TO_SECTOR[_canon(alias)] = tgt

def normalize_sector(name: str) -> str:
    """
    入力された業種名/別名を JPX33 の正規名に正規化する。
    マッチできない場合は "未分類" を返す。
    """
    key = _canon(name or "")
    if not key:
        return "未分類"
    # 完全一致
    if key in CANON_TO_SECTOR:
        return CANON_TO_SECTOR[key]
    # 前方/部分一致（短い語は誤爆しやすいので3文字以上に限定）
    # 例: "情報通信" ← "情報通信技術" でもヒットさせる
    for k, v in CANON_TO_SECTOR.items():
        if len(k) >= 3 and key.startswith(k):
            return v
    for k, v in CANON_TO_SECTOR.items():
        if len(k) >= 4 and k in key:
            return v
    return "未分類"

def map_pf_sectors(sectors: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    PFセクター配列（[{sector, mv, ...}, ...]）を正規化して戻す。
    - sector を normalize して 'sector_norm' にも格納
    - 同一セクターを集計（mv を合算, その他のキーは先勝ち）
    - 降順（mv）
    """
    agg: Dict[str, Dict[str, Any]] = {}
    for row in sectors or []:
        raw = (row.get("sector") or "").strip()
        sec = normalize_sector(raw)
        mv = float(row.get("mv") or 0.0)
        slot = agg.setdefault(sec, {"sector": sec, "mv": 0.0})
        slot["mv"] += mv
        # 代表値として元の主要キーを残したい場合は必要に応じて
        for k in ("rate", "share_pct"):
            if k in row and k not in slot:
                slot[k] = row[k]
    out = list(agg.values())
    out.sort(key=lambda x: float(x.get("mv") or 0.0), reverse=True)
    return out

def jpx33_coverage(rs_table: Dict[str, Dict[str, Any]]) -> Dict[str, bool]:
    """
    RSテーブルのカバレッジを可視化（正規名ごとに有無）。
    例: {"情報・通信業": True, "電気機器": False, ...}
    """
    have = {normalize_sector(k): True for k in (rs_table or {}).keys()}
    return {sec: bool(have.get(sec)) for sec in JPX33}