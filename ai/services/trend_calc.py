from __future__ import annotations
from typing import List, Dict
import statistics

def _sma(series: List[float], n: int) -> float:
    if not series or len(series) < n: return 0.0
    return sum(series[-n:]) / n

def _slope(series: List[float], n: int) -> float:
    """
    簡易傾き：直近n本の回帰風差分。正：上昇、負：下降。軽量・安定優先。
    """
    if not series or len(series) < n: return 0.0
    s = series[-n:]
    xbar = (n+1)/2
    ybar = sum(s)/n
    num = sum((i+1 - xbar)*(y - ybar) for i, y in enumerate(s))
    den = sum((i+1 - xbar)**2 for i in range(n))
    return num/den if den else 0.0

def _vol_spike(vols: List[int], n_ref: int=20) -> float:
    if not vols or len(vols) < n_ref: return 1.0
    recent = statistics.fmean(vols[-5:]) if len(vols)>=5 else statistics.fmean(vols)
    ref = statistics.fmean(vols[-n_ref:])
    return (recent / ref) if ref else 1.0

def calc_snapshot(ohlcv: Dict[str, List], index_rel: float=1.0) -> Dict:
    """
    ohlcv: {'close':[...], 'volume':[...]}
    返り値：TrendResultにそのまま渡せる指標セット
    """
    closes = ohlcv.get('close', [])
    vols   = ohlcv.get('volume', [])
    if not closes: return dict(valid=False)

    last = float(closes[-1])
    ma5  = _sma(closes, 5)
    ma20 = _sma(closes, 20)
    ma60 = _sma(closes, 60)

    daily  = _slope(closes, 10)     # 日足
    weekly = _slope(closes, 10*5)   # 5営業日×10 ≒10週
    monthly= _slope(closes, 20*5)   # 5営業日×20 ≒20週 ~ 月相当

    vspike = _vol_spike(vols, 20)

    return dict(
        valid=True,
        last_price=last,
        last_volume=int(vols[-1] if vols else 0),
        ma5=ma5, ma20=ma20, ma60=ma60,
        daily_slope=daily,
        weekly_trend=weekly,
        monthly_trend=monthly,
        vol_spike=vspike,
        rs_index=float(index_rel),
    )