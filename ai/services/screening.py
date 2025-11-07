from __future__ import annotations
from dataclasses import dataclass
from typing import List

from ai.models import TrendResult
from ai.services.scoring import Factors, compute_score, compute_stars


@dataclass
class TrendPack:
    d: str
    w: str
    m: str


@dataclass
class Prices:
    entry: float
    tp: float
    sl: float


@dataclass
class Qty:
    shares: int
    capital: float
    pl_plus: float
    pl_minus: float
    r: float


@dataclass
class Candidate:
    code: str
    name: str
    sector_jp: str           # ← ここを sector ではなく sector_jp に統一
    score: int
    stars: int
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
    # 強い順の素引き
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
        qty = Qty(shares=0, capital=0.0, pl_plus=0.0, pl_minus=0.0, r=1.0)

        items.append(Candidate(
            code=tr.code,
            name=tr.name or tr.code,
            sector_jp=(tr.sector_jp or "不明"),   # ← ここで確定して持つ
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

    # 強い順に上から並べる
    items.sort(key=lambda x: (-x.score, -x.stars))
    return items[:10]