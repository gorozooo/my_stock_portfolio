from __future__ import annotations
from typing import List, Dict, Any, Optional
from decimal import Decimal
from django.db.models import QuerySet
from ai.models import TrendResult
from ai.services.scoring import compute_score, stars_from_confidence


def _normalize_code(val) -> str:
    s = str(val).strip()
    if '.' in s:
        s = s.split('.', 1)[0]
    return s


def _coerce_dir(val: Optional[object]) -> Optional[str]:
    """
    'up'/'flat'/'down' のいずれかを返す。
    - 数値(Decimal/float/int): >0 up, ==0 flat, <0 down
    - 文字列: 'up'/'flat'/'down' を許可
    """
    if val is None:
        return None
    if isinstance(val, str):
        s = val.strip().lower()
        if s in ('up', 'flat', 'down'):
            return s
        # 数字文字列なら数値扱い
        try:
            num = float(s)
        except Exception:
            return None
        if num > 0: return 'up'
        if num < 0: return 'down'
        return 'flat'
    if isinstance(val, (int, float, Decimal)):
        if float(val) > 0: return 'up'
        if float(val) < 0: return 'down'
        return 'flat'
    return None


def _trend_tuple(r: TrendResult) -> Dict[str, str]:
    """
    TrendResult から (日/週/月) の向きを辞書で返す。
      - 日足:  daily_slope (numeric)
      - 週足:  weekly_trend (numeric: +1/0/-1)
      - 月足:  monthly_trend (numeric: +1/0/-1)
    """
    d = _coerce_dir(getattr(r, 'daily_slope', None))
    w = _coerce_dir(getattr(r, 'weekly_trend', None))
    m = _coerce_dir(getattr(r, 'monthly_trend', None))
    # None は 'flat' に落とす（安定性）
    d = d or 'flat'
    w = w or 'flat'
    m = m or 'flat'
    return {'d': d, 'w': w, 'm': m}


def _dedup(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """7203 / 7203.0 の重複を最初の1件に統合。"""
    seen = {}
    for f in items:
        code = f['code']
        if code not in seen:
            seen[code] = f
    return list(seen.values())


def _base_qs(limit_hint: int) -> QuerySet:
    """
    仮の上位を広めに取り、その後 score で絞る。
    """
    n = max(limit_hint * 3, 60)
    return (TrendResult.objects
            .only('code','name','sector_jp','last_price',
                  'weekly_trend','monthly_trend','rs_index','vol_spike',
                  'daily_slope','confidence')
            .order_by('-weekly_trend', '-monthly_trend', '-vol_spike', '-rs_index')[:n])


def fetch_top_trend_candidates(limit: int = 30) -> List[Dict[str, Any]]:
    """
    方向（d/w/m）を正規化し、score/⭐️を算出。
    コード正規化＆重複排除後、score降順で返却。
    """
    raw: List[Dict[str, Any]] = []
    for r in _base_qs(limit):
        code = _normalize_code(r.code)
        trend = _trend_tuple(r)
        strength = float(r.rs_index or 1.0)
        volb = float(r.vol_spike or 1.0)
        price = float(r.last_price or 0.0)
        conf = float(r.confidence) if r.confidence is not None else None

        item = dict(
            code=code,
            name=r.name or '',
            sector=r.sector_jp or '',
            price=price,
            trend_d=trend['d'],
            trend_w=trend['w'],
            trend_m=trend['m'],
            strength=strength,
            vol_boost=volb,
            confidence=conf,
        )
        # スコア/⭐️
        item['score'] = compute_score(item)
        ups = (1 if trend['d']=='up' else 0) + (1 if trend['w']=='up' else 0) + (1 if trend['m']=='up' else 0)
        item['stars'] = stars_from_confidence(item['confidence'], fallback_ups=ups)
        raw.append(item)

    dedup = _dedup(raw)
    dedup.sort(key=lambda x: x['score'], reverse=True)
    return dedup[:limit]


def fetch_account_caps() -> Dict[str, Any]:
    """後で portfolio/holdings/cash と連携するまでのダミー枠。"""
    return dict(
        cash_buyable=2_000_000,
        nisa_room=1_200_000,
        margin_power=3_000_000,
    )