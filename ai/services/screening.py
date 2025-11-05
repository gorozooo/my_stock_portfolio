from __future__ import annotations
from dataclasses import dataclass
from typing import List
from ai.infra.repositories import fetch_top_trend_candidates

# ---- 表示用の軽量DTO ----
@dataclass
class TrendVec:
    d: str  # 'up' | 'flat' | 'down'
    w: str
    m: str

@dataclass
class PricePlan:
    entry: float
    tp: float
    sl: float

@dataclass
class QtyPlan:
    shares: int
    capital: int
    pl_plus: int
    pl_minus: int
    r: float

@dataclass
class Candidate:
    code: str
    name: str
    sector: str
    score: int      # 0-100
    stars: int      # 1-5
    trend: TrendVec
    prices: PricePlan
    qty: QtyPlan
    reasons: List[str]

# ---- 価格目安・数量目安（軽量な仮版。後でquantityサービスに置換） ----
def _price_plan(price: float) -> PricePlan:
    # 目安：エントリ=現値、TP=+4%、SL=-2%（R=2.0）
    entry = round(price, 2)
    tp    = round(price * 1.04, 2)
    sl    = round(price * 0.98, 2)
    return PricePlan(entry=entry, tp=tp, sl=sl)

def _qty_plan(price: float) -> QtyPlan:
    # 目安：20万円枠で計算（後で cash / NISA / 信用余力連動に差し替え）
    budget = 200_000
    shares = max(1, int(budget // max(1, price)))
    capital = int(shares * price)
    pl_plus = int(shares * (price*1.04 - price))
    pl_minus= int(shares * (price - price*0.98))
    r = 2.0
    return QtyPlan(shares=shares, capital=capital, pl_plus=pl_plus, pl_minus=pl_minus, r=r)

def _reasons(item) -> List[str]:
    rs = item.get('strength', 1.0)
    vol = item.get('vol_boost', 1.0)
    rs_txt = "指数比強め" if rs > 1.05 else ("中立" if rs >= 0.97 else "弱め")
    vol_txt = "出来高増加" if vol > 1.2 else ("平常" if vol >= 0.9 else "出来高減少")
    d = {'up':'上昇','flat':'横ばい','down':'下降'}
    return [
        f"週足: {d.get(item['trend_w'],'?')}",
        f"月足: {d.get(item['trend_m'],'?')}",
        f"相対強度: {rs_txt}",
        f"出来高: {vol_txt}",
        "直近の方向性はUIで確認"
    ]

# ---- 公開API ----
def generate_top10_candidates() -> List[Candidate]:
    raw = fetch_top_trend_candidates(limit=30)  # 重複排除＆score/starsは内部で完成
    top = raw[:10]
    out: List[Candidate] = []
    for it in top:
        price = float(it.get('price', 0.0)) or 0.0
        out.append(Candidate(
            code=it['code'],
            name=it['name'],
            sector=it['sector'],
            score=int(it['score']),
            stars=int(it['stars']),
            trend=TrendVec(d=it['trend_d'], w=it['trend_w'], m=it['trend_m']),
            prices=_price_plan(price),
            qty=_qty_plan(price),
            reasons=_reasons(it),
        ))
    return out