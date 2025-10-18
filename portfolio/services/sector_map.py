# -*- coding: utf-8 -*-
from __future__ import annotations
import os, json, unicodedata, re
from typing import List, Dict, Any, Tuple, Optional
from django.conf import settings

# ====== 東証33業種（TOPIX-33）=======
CANON_33: Tuple[str, ...] = (
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
)

UNCLASSIFIED = "未分類"

# ====== 組み込みエイリアス（よくある表記ゆれ） ======
# ※まずここで幅広く吸収。足りない分は JSON で追加可能（下の load_extra_aliases 参照）
BUILTIN_ALIASES: Dict[str, str] = {
    # 情報・通信業
    "情報通信": "情報・通信業", "情報・通信": "情報・通信業", "情報通信業": "情報・通信業",
    "it": "情報・通信業", "software": "情報・通信業", "ソフトウェア": "情報・通信業",
    "telecom": "情報・通信業", "通信": "情報・通信業",
    # 電気機器
    "電機": "電気機器", "エレクトロニクス": "電気機器", "electronics": "電気機器",
    # 機械
    "産業機械": "機械", "machinery": "機械",
    # 輸送用機器
    "自動車": "輸送用機器", "auto": "輸送用機器", "automobile": "輸送用機器",
    # 精密機器
    "精密": "精密機器", "medical device": "精密機器",
    # 化学
    "chem": "化学", "ケミカル": "化学",
    # 医薬品
    "pharma": "医薬品", "製薬": "医薬品",
    # 食料品
    "食品": "食料品", "food": "食料品",
    # 小売業
    "小売": "小売業", "retail": "小売業", "ec": "小売業",
    # 卸売業
    "卸売": "卸売業", "wholesale": "卸売業",
    # 銀行業
    "銀行": "銀行業", "banks": "銀行業",
    # 証券、商品先物取引業
    "証券": "証券、商品先物取引業", "ブローカー": "証券、商品先物取引業",
    # 保険業
    "保険": "保険業", "insurance": "保険業",
    # その他金融業
    "リース": "その他金融業", "ノンバンク": "その他金融業", "消費者金融": "その他金融業", "fintech": "その他金融業",
    # 不動産業
    "不動産": "不動産業", "reit": "不動産業", "デベロッパー": "不動産業",
    # サービス業
    "サービス": "サービス業", "外食": "サービス業", "人材": "サービス業",
    # 建設業
    "建設": "建設業", "ゼネコン": "建設業",
    # 鉄鋼 / 非鉄金属 / 金属製品
    "スチール": "鉄鋼", "nonferrous": "非鉄金属", "加工金属": "金属製品",
    # ガラス・土石製品
    "ガラス土石": "ガラス・土石製品", "セメント": "ガラス・土石製品",
    # 石油・石炭製品
    "石油": "石油・石炭製品", "エネルギー精製": "石油・石炭製品", "refining": "石油・石炭製品",
    # ゴム製品
    "タイヤ": "ゴム製品", "rubber": "ゴム製品",
    # 陸運 / 海運 / 空運 / 倉庫
    "物流": "倉庫・運輸関連業", "トラック": "陸運業", "鉄道": "陸運業",
    "shipping": "海運業", "航空": "空運業", "airline": "空運業", "倉庫": "倉庫・運輸関連業",
    # 電気・ガス業
    "電力ガス": "電気・ガス業", "utility": "電気・ガス業", "utilities": "電気・ガス業",
    # パルプ・紙
    "紙": "パルプ・紙", "pulp": "パルプ・紙",
    # 繊維製品
    "アパレル": "繊維製品", "textile": "繊維製品",
    # 水産・農林業
    "水産": "水産・農林業", "農林": "水産・農林業", "agri": "水産・農林業",
    # その他製品
    "玩具": "その他製品", "文具": "その他製品",
)

# ====== 追加エイリアスを MEDIA_ROOT から読み込む ======
# 例: media/advisor/sector_aliases.json
# {
#   "ai/半導体": "電気機器",
#   "platform": "情報・通信業",
#   "鉄": "鉄鋼"
# }
def _aliases_json_path() -> str:
    base = getattr(settings, "MEDIA_ROOT", "") or os.getcwd()
    return os.path.join(base, "advisor", "sector_aliases.json")

_EXTRA_ALIASES_CACHE: Optional[Dict[str, str]] = None

def load_extra_aliases() -> Dict[str, str]:
    global _EXTRA_ALIASES_CACHE
    if _EXTRA_ALIASES_CACHE is not None:
        return _EXTRA_ALIASES_CACHE
    path = _aliases_json_path()
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                obj = json.load(f)
            if isinstance(obj, dict):
                # 小文字・正規化キーへ寄せる
                _EXTRA_ALIASES_CACHE = { _key_norm(k): v for k, v in obj.items() if isinstance(v, str) }
                return _EXTRA_ALIASES_CACHE
    except Exception:
        pass
    _EXTRA_ALIASES_CACHE = {}
    return _EXTRA_ALIASES_CACHE

# ====== 正規化ヘルパ ======
def _text_simplify(s: str) -> str:
    """全角→半角、記号の統一、英字小文字化"""
    s = unicodedata.normalize("NFKC", s or "")
    s = s.replace("・", "").replace("，", ",").replace("　", " ")
    s = s.replace("／", "/").replace("・", "")
    return s.strip().lower()

def _key_norm(s: str) -> str:
    # 業種名の突合用。句読点やスペース、記号を緩く無視
    t = _text_simplify(s)
    t = re.sub(r"[\s&\-/,_]+", "", t)  # スペース・&・-・_・,・/ を削除
    return t

_CANON_KEYS = { _key_norm(x): x for x in CANON_33 }

def _lookup_builtin(key_norm: str) -> Optional[str]:
    # 組み込みエイリアス → Canon
    v = BUILTIN_ALIASES.get(key_norm)
    if v in CANON_33:
        return v
    # BUILTIN_ALIASES のキーは人間可読も混ざるので、キー側も正規化して当てる
    for k, canon in BUILTIN_ALIASES.items():
        if _key_norm(k) == key_norm and canon in CANON_33:
            return canon
    return None

def _lookup_extra(key_norm: str) -> Optional[str]:
    ex = load_extra_aliases()
    v = ex.get(key_norm)
    if v in CANON_33:
        return v
    # 値が Canon でなければ弾く（守り）
    return None

# ====== 公開API ======
def normalize_sector(name: Optional[str]) -> str:
    """
    任意の業種表記を TOPIX-33 の Canonical 名に正規化。
    未判定は「未分類」。
    """
    if not name:
        return UNCLASSIFIED
    # 完全一致（記号含む）を先に
    if name in CANON_33:
        return name
    key = _key_norm(name)

    # 1) Canon キー（軽微表記ゆれ）
    if key in _CANON_KEYS:
        return _CANON_KEYS[key]

    # 2) 追加エイリアス（ユーザー定義）
    v = _lookup_extra(key)
    if v:
        return v

    # 3) 組み込みエイリアス
    v = _lookup_builtin(key)
    if v:
        return v

    # 4) 緩い前方/部分一致（例: “電機”→電気機器）
    for ck, canon in _CANON_KEYS.items():
        if ck.startswith(key) or key.startswith(ck):
            return canon

    return UNCLASSIFIED


def map_pf_sectors(sectors: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    PFセクター配列([{sector, mv, cost?, rate?}...])を正規化&集約。
    戻り値は mv/cost を合算し、share_pct と rate を再計算する。
    """
    if not sectors:
        return []

    bucket: Dict[str, Dict[str, float]] = {}
    total_mv = 0.0

    for row in sectors:
        raw = (row.get("sector") or "").strip()
        mv = float(row.get("mv") or 0.0)
        cost = float(row.get("cost") or 0.0)
        sec = normalize_sector(raw)
        acc = bucket.setdefault(sec, {"mv": 0.0, "cost": 0.0})
        acc["mv"] += mv
        acc["cost"] += cost
        total_mv += mv

    total_mv = max(total_mv, 1.0)

    # 出力整形（mv降順）
    out: List[Dict[str, Any]] = []
    for sec, agg in bucket.items():
        mv = float(agg["mv"])
        cost = float(agg["cost"])
        share_pct = mv / total_mv * 100.0
        # rate が事前に来る場合もあるが、ここでは “構成比%” を rate として再定義
        out.append({
            "sector": sec,
            "mv": round(mv, 2),
            "cost": round(cost, 2),
            "share_pct": round(share_pct, 2),
            "rate": round(share_pct, 2),
        })

    out.sort(key=lambda x: x["mv"], reverse=True)
    return out