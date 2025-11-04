from typing import List, Dict, Any
from ai.models import TrendResult

def fetch_top_trend_candidates(limit: int = 30) -> List[Dict[str, Any]]:
    """
    TrendResultをソートして上位抽出。
    並びは「週足>月足>出来高スパイク>相対強度」を基礎軸に（軽量＆説明可能）。
    """
    qs = (TrendResult.objects
          .order_by('-weekly_trend', '-monthly_trend', '-vol_spike', '-rs_index')[:limit])

    out: List[Dict[str, Any]] = []
    for r in qs:
        out.append(dict(
            code=r.code,
            name=r.name,
            sector=r.sector_jp,
            price=float(r.last_price),
            trend_d=r.dir_d,
            trend_w=r.dir_w,
            trend_m=r.dir_m,
            strength=float(r.rs_index),
            vol_boost=float(r.vol_spike),
        ))
    return out

def fetch_account_caps() -> Dict[str, Any]:
    # TODO: 後で cash/holdings から本物を取得
    return dict(
        cash_buyable=2_000_000,
        nisa_room=1_200_000,
        margin_power=3_000_000,
    )