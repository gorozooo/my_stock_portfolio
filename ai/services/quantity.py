from typing import Dict
import math

def propose_quantity(price: float, caps: Dict, mode: str = 'cash_first') -> Dict:
    """
    NISA優先→現物→不足は信用…の指針で数量を出す簡易版。
    後で “日次リスク上限” や “セクター分散” を反映。
    """
    buyable = caps.get('cash_buyable', 0)
    nisa = caps.get('nisa_room', 0)
    use_cash = min(buyable, nisa if mode == 'cash_first' else buyable)
    shares = int(use_cash // price)
    shares = max(0, shares)

    # 損切り幅（3%想定）・利確幅（5%想定）→ 後でATR/レジに差し替え
    sl_gap = price * 0.03
    tp_gap = price * 0.05
    pl_minus = int(math.floor(shares * sl_gap))
    pl_plus = int(math.floor(shares * tp_gap))
    r = round((pl_plus / pl_minus), 2) if pl_minus > 0 else 0.0

    return dict(
        shares=shares,
        capital=int(shares * price),
        pl_plus=pl_plus,
        pl_minus=pl_minus,
        r=r
    )