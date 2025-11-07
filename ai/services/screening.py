from __future__ import annotations
from dataclasses import dataclass
from typing import List, Dict
import csv
from pathlib import Path

from ai.models import TrendResult
from ai.services.scoring import Factors, compute_score, compute_stars

# ---------- セクター正規化（恒久処置） ----------
_SECT33: Dict[int, str] = {
    50:'水産・農林業', 51:'鉱業', 52:'建設業', 53:'食料品', 54:'繊維製品', 55:'パルプ・紙',
    56:'化学', 57:'医薬品', 58:'石油・石炭製品', 59:'ゴム製品', 60:'ガラス・土石製品',
    61:'鉄鋼', 62:'非鉄金属', 63:'金属製品', 64:'機械', 65:'電気機器', 66:'輸送用機器',
    67:'精密機器', 68:'その他製品', 69:'電気・ガス業', 70:'陸運業', 71:'海運業', 72:'空運業',
    73:'倉庫・運輸関連業', 74:'情報・通信業', 75:'卸売業', 76:'小売業', 77:'銀行業',
    78:'証券、商品先物取引業', 79:'保険業', 80:'その他金融業', 81:'不動産業', 82:'サービス業', 83:'その他',
}

# コード→日本語セクター名のメモリキャッシュ
_code2sector_jp: Dict[str, str] = {}

def _load_code2sector() -> Dict[str, str]:
    """media/jpx_master.csv をCSVモジュールで厳密に読む（クォート対応）。"""
    global _code2sector_jp
    if _code2sector_jp:
        return _code2sector_jp

    p = Path("media/jpx_master.csv")
    m: Dict[str, str] = {}
    if p.exists():
        with p.open("r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            # 期待ヘッダ: code,name,sector
            for row in reader:
                code = (row.get("code") or "").strip()
                # 4桁抽出
                import re
                m4 = re.search(r"(\d{4})", code)
                if not m4:
                    continue
                c4 = m4.group(1)
                s = (row.get("sector") or "").strip()
                # 数値コード(50..83)は日本語へ
                if re.fullmatch(r"\d+(?:\.\d+)?", s or ""):
                    try:
                        n = int(float(s))
                        s = _SECT33.get(n, s)
                    except Exception:
                        pass
                if s:
                    m[c4] = s
    _code2sector_jp = m
    return _code2sector_jp

def _normalize_sector(code: str, sector_like: str | None) -> str:
    """
    - sector_like が日本語ならそのまま
    - 数値なら 50..83 を日本語化
    - それ以外/空なら jpx_master.csv を参照
    - 最後まで無理なら '-'
    """
    s = (sector_like or "").strip()
    import re
    if s and not re.fullmatch(r"\d+(?:\.\d+)?", s):
        return s
    if s:
        try:
            n = int(float(s))
            if 50 <= n <= 83:
                return _SECT33.get(n, "-")
        except Exception:
            pass
    mp = _load_code2sector()
    return mp.get((code or "")[-4:], "-")

# ---------- 候補データ構造 ----------
@dataclass
class TrendPack:
    d: str; w: str; m: str

@dataclass
class Prices:
    entry: float; tp: float; sl: float

@dataclass
class Qty:
    shares: int; capital: float; pl_plus: float; pl_minus: float; r: float

@dataclass
class Candidate:
    code: str; name: str; sector: str
    score: int; stars: int
    trend: TrendPack
    prices: Prices
    reasons: List[str]
    qty: Qty

def _dir_from_num(x: float) -> str:
    return "up" if x > 0 else ("down" if x < 0 else "flat")

def _safe(v, default=0.0):
    try:
        return float(v)
    except Exception:
        return float(default)

def generate_top10_candidates() -> List[Candidate]:
    """
    恒久化ポイント:
      - ここで sector は必ず“日本語名”に正規化して返す（JS補正不要）
      - DBの sector_jp が空でも jpx_master.csv で補完
    """
    qs = TrendResult.objects.order_by("-confidence", "-weekly_trend", "-monthly_trend")[:50]
    items: List[Candidate] = []
    for tr in qs:
        f = Factors(
            daily_slope=_safe(tr.daily_slope),
            weekly_trend=_safe(tr.weekly_trend),
            monthly_trend=_safe(tr.monthly_trend),
            rs_index=max(0.1, _safe(tr.rs_index, 1.0)),
            vol_spike=max(0.1, _safe(tr.vol_spike, 1.0)),
            confidence=max(0.0, min(1.0, _safe(tr.confidence, 0.0))),
        )
        score = compute_score(f)
        stars = compute_stars(score, f.confidence)

        price = _safe(tr.last_price, 0.0)
        prices = Prices(
            entry=round(price, 2),
            tp=round(price * 1.06, 2),
            sl=round(price * 0.97, 2),
        )
        reasons = [
            f"週/月トレンド: {_dir_from_num(f.weekly_trend)}/{_dir_from_num(f.monthly_trend)}",
            f"RS: {f.rs_index:.2f}",
            f"Volume boost: {f.vol_spike:.2f}",
            f"傾き: {f.daily_slope:.2f}",
            f"信頼度: {int(f.confidence*100)}%",
        ]
        # ★ここで必ず日本語化
        sector_jp = _normalize_sector(tr.code, getattr(tr, "sector_jp", None))

        qty = Qty(shares=0, capital=0.0, pl_plus=0.0, pl_minus=0.0, r=1.0)
        items.append(Candidate(
            code=tr.code,
            name=tr.name or tr.code,
            sector=sector_jp,
            score=score,
            stars=stars,
            trend=TrendPack(
                d=_dir_from_num(f.daily_slope),
                w=_dir_from_num(f.weekly_trend),
                m=_dir_from_num(f.monthly_trend),
            ),
            prices=prices,
            reasons=reasons,
            qty=qty,
        ))
    items.sort(key=lambda x: (-x.score, -x.stars))
    return items[:10]