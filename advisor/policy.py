# advisor/policy.py
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, Dict


@dataclass
class PolicyInput:
    ticker: str
    segment: str               # 例: "短期（5〜10日）", "中期（20〜45日）"
    theme_score: float         # 0~1
    ai_win_prob: float         # 0~1
    last_price: Optional[float] = None  # 現在値/終値が入れば使う
    credit_balance: Optional[float] = None  # 信用余力（円）
    risk_per_trade: float = 0.01          # 1トレードの許容損失（%）初期1%

@dataclass
class PolicyOutput:
    weekly_trend: str              # "up" / "flat" / "down"
    overall_score: int             # 0~100
    entry_price_hint: int          # 円
    tp_pct: float
    sl_pct: float
    tp_price: int
    sl_price: int
    position_size_hint: Optional[int]  # 株数
    need_cash: Optional[int]           # 必要資金（円）
    tp_prob: Optional[float]
    sl_prob: Optional[float]


def _decide_weekly_trend(theme_score: float, win_prob: float) -> str:
    score = 0.7*win_prob + 0.3*theme_score
    if score >= 0.62: return "up"
    if score >= 0.48: return "flat"
    return "down"

def _decide_tp_sl(segment: str) -> Dict[str, float]:
    s = segment
    if "短期" in s:
        return {"tp": 0.06, "sl": 0.02}    # +6% / -2%
    if "中期" in s:
        return {"tp": 0.10, "sl": 0.03}    # +10% / -3%
    # 長期など
    return {"tp": 0.12, "sl": 0.05}

def _fallback_price(ticker: str) -> float:
    # デモ用の仮値（無いよりマシ）。実運用では株価取得に置換。
    seeds = {"8035.T": 12450, "6758.T": 14680, "7203.T": 3150, "8306.T": 1470, "8267.T": 3180}
    return float(seeds.get(ticker, 3000))

def _round_yen(x: float) -> int:
    return int(round(x))

def _position_size(entry: float, sl_price: float, credit_balance: Optional[float], risk_per_trade: float) -> (Optional[int], Optional[int]):
    if not credit_balance or entry <= 0:
        return None, None
    stop_value = max(1.0, entry - sl_price)  # 円
    risk_budget = max(1.0, credit_balance * risk_per_trade)
    shares = int(risk_budget // stop_value)
    shares = max(0, shares)
    need_cash = int(round(shares * entry))
    return shares if shares > 0 else None, need_cash if shares > 0 else None

def _overall(theme_score: float, win_prob: float) -> int:
    return int(round((0.7*win_prob + 0.3*theme_score) * 100))

def _prob_split(win_prob: float) -> (float, float):
    # デモ配分：勝率の一部をTPに、負けの一部をSLに割当（残りはどちらでもない）
    tp = max(0.0, min(1.0, win_prob * 0.46))
    sl = max(0.0, min(1.0, (1.0 - win_prob) * 0.30))
    return tp, sl


def evaluate(p: PolicyInput) -> PolicyOutput:
    last = p.last_price or _fallback_price(p.ticker)
    tp_sl = _decide_tp_sl(p.segment)
    tp_pct, sl_pct = tp_sl["tp"], tp_sl["sl"]

    entry = last  # デモ：基本は現在値、将来は戦略により上/下バッファ
    tp_price = _round_yen(entry * (1 + tp_pct))
    sl_price = _round_yen(entry * (1 - sl_pct))

    size, need = _position_size(entry, sl_price, p.credit_balance, p.risk_per_trade)
    weekly = _decide_weekly_trend(p.theme_score, p.ai_win_prob)
    overall = _overall(p.theme_score, p.ai_win_prob)
    tp_prob, sl_prob = _prob_split(p.ai_win_prob)

    return PolicyOutput(
        weekly_trend=weekly,
        overall_score=overall,
        entry_price_hint=_round_yen(entry),
        tp_pct=tp_pct, sl_pct=sl_pct,
        tp_price=tp_price, sl_price=sl_price,
        position_size_hint=size,
        need_cash=need,
        tp_prob=tp_prob, sl_prob=sl_prob
    )