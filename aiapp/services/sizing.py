"""
Entry/TP/SL/数量/想定PLの計算。M1は定数係数で簡易。
"""
def size_position(entry, tp, sl, equity=3_000_000, risk_rate=0.02, lot=100):
    risk_yen = equity * risk_rate
    one_share_risk = abs(entry - sl)
    if one_share_risk <= 0:
        return 0, 0, 0
    shares = max(lot, int(risk_yen / one_share_risk / lot) * lot)
    funds = shares * entry
    pl_gain = shares * abs(tp - entry)
    pl_loss = shares * abs(entry - sl)
    return shares, funds, pl_gain, pl_loss
