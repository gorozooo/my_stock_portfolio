from __future__ import annotations
from dataclasses import dataclass
from typing import Optional

DEFAULT_RISK_RATE_AGGR = 0.02  # 短期×攻め 既定：2%
DEFAULT_LOT = 100              # 日本株の最小売買単位の想定

@dataclass
class SizingInput:
    entry: float
    tp: float
    sl: float
    equity: float
    risk_rate: float = DEFAULT_RISK_RATE_AGGR
    lot: int = DEFAULT_LOT
    max_funds: Optional[float] = None
    max_shares: Optional[int] = None

@dataclass
class SizingResult:
    shares: int
    funds: float
    pl_gain: float
    pl_loss: float
    rr: Optional[float]
    allow_risk_yen: float
    one_share_risk: float
    reason: str

def _round_down_to_lot(value: int, lot: int) -> int:
    if lot <= 0:
        return max(0, value)
    return max(0, (value // lot) * lot)

def size_position(inp: SizingInput) -> SizingResult:
    if inp.entry <= 0 or inp.tp <= 0 or inp.sl <= 0:
        return SizingResult(0,0.0,0.0,0.0,None,0.0,0.0,"価格が不正（0以下）")
    if inp.equity <= 0:
        return SizingResult(0,0.0,0.0,0.0,None,0.0,0.0,"資産（equity）が0以下")
    if inp.risk_rate <= 0:
        return SizingResult(0,0.0,0.0,0.0,None,0.0,0.0,"リスク率（risk_rate）が0以下")

    allow_risk_yen = float(inp.equity) * float(inp.risk_rate)
    one_share_risk = abs(float(inp.entry) - float(inp.sl))

    if one_share_risk <= 0:
        return SizingResult(0,0.0,0.0,0.0,None,allow_risk_yen,one_share_risk,"EntryとSLの差が0")

    theoretical_shares = int(allow_risk_yen // one_share_risk)
    shares = _round_down_to_lot(theoretical_shares, inp.lot)
    if shares <= 0:
        return SizingResult(0,0.0,0.0,0.0,None,allow_risk_yen,one_share_risk,"許容内で最小単位に届かない")

    # 上限制約
    if inp.max_shares is not None and inp.max_shares > 0 and shares > inp.max_shares:
        shares = _round_down_to_lot(inp.max_shares, inp.lot)
        reason = "max_sharesで縮小"
    else:
        reason = "OK"

    funds = shares * float(inp.entry)
    if inp.max_funds is not None and inp.max_funds > 0 and funds > inp.max_funds:
        shares_by_funds = int(inp.max_funds // float(inp.entry))
        shares_by_funds = _round_down_to_lot(shares_by_funds, inp.lot)
        if shares_by_funds <= 0:
            return SizingResult(0,0.0,0.0,0.0,None,allow_risk_yen,one_share_risk,"max_fundsで最小単位に届かない")
        shares = min(shares, shares_by_funds)
        funds = shares * float(inp.entry)
        reason = "max_fundsで縮小" if reason == "OK" else (reason + " / max_fundsで縮小")

    pl_gain = shares * abs(float(inp.tp) - float(inp.entry))
    pl_loss = shares * abs(float(inp.entry) - float(inp.sl))
    rr = (pl_gain / pl_loss) if pl_loss > 0 else None

    return SizingResult(
        shares=shares, funds=funds, pl_gain=pl_gain, pl_loss=pl_loss, rr=rr,
        allow_risk_yen=allow_risk_yen, one_share_risk=one_share_risk, reason=reason
    )

def size_aggressive_short(entry: float, tp: float, sl: float, equity: float,
                          lot: int = DEFAULT_LOT,
                          max_funds: Optional[float] = None,
                          max_shares: Optional[int] = None) -> SizingResult:
    inp = SizingInput(entry=entry, tp=tp, sl=sl, equity=equity,
                      risk_rate=DEFAULT_RISK_RATE_AGGR, lot=lot,
                      max_funds=max_funds, max_shares=max_shares)
    return size_position(inp)
