from typing import List, Dict, Any
from ai.models import TrendResult
from ai.services.scoring import compute_score, stars_from_confidence

def _dedup(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = {}
    for f in items:
        code = f['code']
        if code not in seen:
            seen[code] = f
    return list(seen.values())

def fetch_top_trend_candidates(limit: int = 30) -> List[Dict[str, Any]]:
    """
    TrendResultをソートして上位抽出。
    並びは「週足>月足>出来高スパイク>相対強度」。
    コード正規化＆重複排除、confidence→⭐️変換までここで完結。
    """
    qs = (TrendResult.objects
          .order_by('-weekly_trend', '-monthly_trend', '-vol_spike', '-rs_index')[:max(limit*2, 50)])

    raw: List[Dict[str, Any]] = []
    for r in qs:
        code = str(r.code).split('.', 1)[0]  # "7203.0"→"7203"
        ups = (1 if r.dir_d == 'up' else 0) + (1 if r.dir_w == 'up' else 0) + (1 if r.dir_m == 'up' else 0)
        item = dict(
            code=code,
            name=r.name,
            sector=r.sector_jp,
            price=float(r.last_price),
            trend_d=r.dir_d, trend_w=r.dir_w, trend_m=r.dir_m,
            strength=float(r.rs_index),
            vol_boost=float(r.vol_spike),
            confidence=float(r.confidence) if r.confidence is not None else None,
        )
        # スコア/⭐️
        item['score'] = compute_score(item)
        item['stars'] = stars_from_confidence(item['confidence'], fallback_ups=ups)
        raw.append(item)

    dedup = _dedup(raw)
    dedup.sort(key=lambda x: x['score'], reverse=True)
    return dedup[:limit]

def fetch_account_caps() -> Dict[str, Any]:
    # TODO: 後で cash/holdings から本物を取得
    return dict(
        cash_buyable=2_000_000,
        nisa_room=1_200_000,
        margin_power=3_000_000,
    )