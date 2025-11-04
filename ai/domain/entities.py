from dataclasses import dataclass
from typing import List, Literal, Optional

TrendDir = Literal['up', 'flat', 'down']

@dataclass(frozen=True)
class TrendTriple:
    d: TrendDir  # daily
    w: TrendDir  # weekly
    m: TrendDir  # monthly

@dataclass(frozen=True)
class PriceTargets:
    entry: float
    tp: float
    sl: float

@dataclass(frozen=True)
class QuantityPlan:
    shares: int
    capital: int
    pl_plus: int
    pl_minus: int
    r: float

@dataclass(frozen=True)
class Candidate:
    name: str
    code: str
    sector: str
    score: int          # 0-100 → 表示は「92点」
    stars: int          # 1-5
    trend: TrendTriple
    reasons: List[str]  # 5つ（懸念含む）
    prices: PriceTargets
    qty: QuantityPlan