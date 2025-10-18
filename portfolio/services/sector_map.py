# portfolio/services/sector_map.py
# -*- coding: utf-8 -*-
from __future__ import annotations
from typing import Dict, List, Any

# 東証33業種（正規化後の“代表名”）
N33: List[str] = [
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
    "情報・通信",
    "卸売業",
    "小売業",
    "銀行業",
    "証券・商品先物",
    "保険業",
    "その他金融業",
    "不動産業",
    "サービス業",
]

# よくある別表記・略称・英語名→代表名 のマップ
ALIASES: Dict[str, str] = {
    # 情報・通信
    "情報通信": "情報・通信",
    "情報・通信業": "情報・通信",
    "IT": "情報・通信",
    "ソフトウェア": "情報・通信",
    "通信": "情報・通信",
    "internet": "情報・通信",
    "software": "情報・通信",
    "telecom": "情報・通信",

    # 電気機器
    "電機": "電気機器",
    "エレクトロニクス": "電気機器",
    "electronics": "電気機器",
    "semiconductor": "電気機器",
    "半導体": "電気機器",

    # 機械
    "産機": "機械",
    "machinery": "機械",

    # 輸送用機器
    "自動車": "輸送用機器",
    "auto": "輸送用機器",
    "transport equipment": "輸送用機器",

    # 精密機器
    "医療機器": "精密機器",
    "optics": "精密機器",
    "precision": "精密機器",

    # 化学
    "素材・化学": "化学",
    "material": "化学",
    "chemicals": "化学",

    # 医薬品
    "pharma": "医薬品",
    "製薬": "医薬品",

    # 石油・石炭製品
    "エネルギー・石油": "石油・石炭製品",
    "oil": "石油・石炭製品",
    "petroleum": "石油・石炭製品",

    # 鉄鋼 / 非鉄金属 / 金属製品
    "金属": "金属製品",
    "steel": "鉄鋼",
    "非鉄": "非鉄金属",
    "nonferrous": "非鉄金属",

    # ガラス・土石製品
    "セメント": "ガラス・土石製品",

    # ゴム製品
    "タイヤ": "ゴム製品",
    "rubber": "ゴム製品",

    # 食料品
    "食品": "食料品",
    "food": "食料品",
    "beverage": "食料品",

    # 小売 / 卸売
    "retail": "小売業",
    "wholesale": "卸売業",

    # 銀行 / 証券 / 保険 / その他金融
    "金融（銀行）": "銀行業",
    "bank": "銀行業",
    "証券": "証券・商品先物",
    "securities": "証券・商品先物",
    "保険": "保険業",
    "insurance": "保険業",
    "リース": "その他金融業",
    "消費者金融": "その他金融業",
    "カード": "その他金融業",
    "finance": "その他金融業",

    # 不動産
    "不動産": "不動産業",
    "real estate": "不動産業",
    "reit": "不動産業",

    # サービス
    "サービス": "サービス業",
    "service": "サービス業",

    # 交通・運輸
    "物流": "倉庫・運輸関連業",
    "運輸": "倉庫・運輸関連業",
    "airlines": "空運業",
    "shipping": "海運業",
    "rail": "陸運業",

    # エネルギー公共
    "utilities": "電気・ガス業",

    # 農林水産
    "水産": "水産・農林業",
    "農林": "水産・農林業",

    # 建設
    "建設": "建設業",
    "construction": "建設業",

    # パルプ・紙
    "紙": "パルプ・紙",

    # その他製品
    "consumer products": "その他製品",
}

# 事前の簡易正規化テーブル（記号/空白など）
REPLACE_TABLE = {
    "　": " ",
    "/": "・",
    "-": "・",
    "_": "・",
}

def _simplify(s: str) -> str:
    s = (s or "").strip()
    for a, b in REPLACE_TABLE.items():
        s = s.replace(a, b)
    return s

def normalize_sector(name: str) -> str:
    """
    入力セクター名を東証33業種いずれかの代表名へ寄せる。
    未判定は「未分類」を返す。
    """
    if not name:
        return "未分類"
    raw = _simplify(str(name))
    low = raw.lower()

    # 完全一致（代表名）
    if raw in N33:
        return raw

    # エイリアス一致
    if low in ALIASES:
        return ALIASES[low]

    # “・”やスペース除去のゆるい一致
    loose = raw.replace("・", "").replace(" ", "")
    for rep in N33:
        if loose == rep.replace("・", "").replace(" ", ""):
            return rep

    return "未分類"

def map_pf_sectors(sectors: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    PFのセクター配列 [{sector, mv, ...}] を正規化・集約して降順で返す。
    - sector は normalize_sector() を適用
    - 同一セクターは mv を加算
    - rate があれば維持、無ければ後段で UI が計算する想定
    """
    agg: Dict[str, Dict[str, Any]] = {}
    for s in sectors or []:
        mv = float(s.get("mv", 0.0) or 0.0)
        sec = normalize_sector(s.get("sector") or "")
        a = agg.setdefault(sec, {"sector": sec, "mv": 0.0})
        a["mv"] += mv

    out = sorted(agg.values(), key=lambda x: x["mv"], reverse=True)
    total_mv = sum(x["mv"] for x in out) or 1.0
    for x in out:
        x["share_pct"] = round(x["mv"] / total_mv * 100.0, 2)
    return out