# -*- coding: utf-8 -*-
from __future__ import annotations
import os, json
from typing import Dict, Any, List, Optional
from django.conf import settings

# デフォルト別名マップ（必要に応じて追記）
_DEFAULT_ALIASES: Dict[str, str] = {
    # JPX33の代表例
    "水産・農林業": "水産・農林業",
    "鉱業": "鉱業",
    "建設業": "建設業",
    "食料品": "食料品",
    "繊維製品": "繊維製品",
    "パルプ・紙": "パルプ・紙",
    "化学": "化学",
    "医薬品": "医薬品",
    "石油・石炭製品": "石油・石炭製品",
    "ゴム製品": "ゴム製品",
    "ガラス・土石製品": "ガラス・土石製品",
    "鉄鋼": "鉄鋼",
    "非鉄金属": "非鉄金属",
    "金属製品": "金属製品",
    "機械": "機械",
    "電気機器": "電気機器",
    "輸送用機器": "輸送用機器",
    "精密機器": "精密機器",
    "その他製品": "その他製品",
    "電気・ガス業": "電気・ガス業",
    "陸運業": "陸運業",
    "海運業": "海運業",
    "空運業": "空運業",
    "倉庫・運輸関連業": "倉庫・運輸関連業",
    "情報・通信業": "情報・通信",
    "情報・通信": "情報・通信",
    "卸売業": "卸売業",
    "小売業": "小売業",
    "銀行業": "銀行業",
    "証券、商品先物取引業": "証券・商品先物",
    "証券・商品先物": "証券・商品先物",
    "保険業": "保険業",
    "その他金融業": "その他金融業",
    "不動産業": "不動産業",
    # よくあるゆらぎ
    "IT": "情報・通信",
    "IT・ソフトウェア": "情報・通信",
    "通信": "情報・通信",
    "テクノロジー": "情報・通信",
    "化学工業": "化学",
    "医療": "医薬品",
    "電力・ガス": "電気・ガス業",
    "運輸": "陸運業",  # 広義→代表に寄せる
    "海運": "海運業",
    "空運": "空運業",
    "半導体": "電気機器",
    "精密": "精密機器",
    "機械・装置": "機械",
    "金属": "非鉄金属",
    "エネルギー": "石油・石炭製品",
    "リテール": "小売業",
    "商社": "卸売業",
    "不動産": "不動産業",
    "銀行": "銀行業",
    "保険": "保険業",
}

def _media_root() -> str:
    return getattr(settings, "MEDIA_ROOT", "") or os.getcwd()

def _alias_path() -> str:
    """
    上書き可能な外部ファイル:
      MEDIA_ROOT/market/sector_aliases.json
      形式: { "テクノロジー": "情報・通信", ... }
    """
    return os.path.join(_media_root(), "market", "sector_aliases.json")

def load_alias_table() -> Dict[str, str]:
    """外部ファイルがあればマージして返す（大文字小文字/全角半角は呼び出し側で揃える前提）"""
    table = dict(_DEFAULT_ALIASES)
    path = _alias_path()
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                ext = json.load(f)
            if isinstance(ext, dict):
                table.update({str(k): str(v) for k, v in ext.items()})
    except Exception:
        # 壊れててもデフォルトだけで続行
        pass
    return table

def normalize_sector(name: Optional[str]) -> str:
    """
    セクター名の正規化（別名→JPX33代表名へ寄せる）
    未知 or 空は "未分類"
    """
    if not name:
        return "未分類"
    src = str(name).strip()
    if not src:
        return "未分類"
    # 余計なスペースや記号の軽い正規化
    src_norm = src.replace("　", " ").replace("・", "・").replace("/", "／").strip()
    table = load_alias_table()
    # 完全一致優先
    if src_norm in table:
        return table[src_norm]
    # 大文字小文字の揺れ（英語想定）対策
    key_l = src_norm.lower()
    for k, v in table.items():
        if k.lower() == key_l:
            return v
    # 先頭一致・含有などの簡易フォールバック（半導体/通信/銀行 など）
    hints = [
        ("半導体", "電気機器"),
        ("通信", "情報・通信"),
        ("IT", "情報・通信"),
        ("銀行", "銀行業"),
        ("保険", "保険業"),
        ("商社", "卸売業"),
        ("小売", "小売業"),
        ("不動産", "不動産業"),
        ("運輸", "陸運業"),
        ("化学", "化学"),
    ]
    for h, dst in hints:
        if h in src_norm:
            return dst
    return "未分類"

def map_pf_sectors(sectors: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    ポートフォリオのセクター配列（{sector, mv, ...}）を正規化して返す
    - sector_norm フィールドを追加
    """
    out: List[Dict[str, Any]] = []
    for s in (sectors or []):
        sec = (s.get("sector") or "").strip()
        norm = normalize_sector(sec)
        row = dict(s)
        row["sector_norm"] = norm
        out.append(row)
    return out