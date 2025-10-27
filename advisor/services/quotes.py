# advisor/services/quotes.py
from __future__ import annotations
from typing import Optional, Dict

# フォールバック価格（無ければNoneを返す）
_FALLBACK_PRICE: Dict[str, int] = {
    "8035.T": 12450,
    "7203.T": 3150,
    "6758.T": 14680,
    "8267.T": 3180,
    "8306.T": 1470,
}

def get_last_price(ticker: str) -> Optional[int]:
    """
    実データ導入ポイント：
      - 証券API/DB から現在値 or 直近終値を返す
      - 取得失敗時は None を返す
    いまはフォールバックのみ
    """
    return _FALLBACK_PRICE.get(ticker.upper())

def get_week_trend_hint(ticker: str) -> Optional[str]:
    """
    実データ導入ポイント（任意）：
      週足の向き "up"|"flat"|"down" を返せるならここで。
      いまは None（上位ロジックが推定）
    """
    return None