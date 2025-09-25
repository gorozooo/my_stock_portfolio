# portfolio/services/quotes.py
from __future__ import annotations
import time
from typing import Dict, Tuple, Optional
import yfinance as yf
from .trend import _normalize_ticker  # 既存の正規化を流用

# in-memory cache: {ticker: (price, ts)}
_CACHE: Dict[str, Tuple[float, float]] = {}
_TTL = 600.0  # 10分

def last_price(code_head: str) -> Optional[float]:
    """'7011' / '167A' -> float  終値/直近価格（失敗時 None）。10分キャッシュ。"""
    t = _normalize_ticker(code_head)  # '7011' -> '7011.T'
    if not t:
        return None
    now = time.time()
    p = _CACHE.get(t)
    if p and now - p[1] < _TTL:
        return p[0]
    try:
        info = getattr(yf.Ticker(t), "fast_info", None) or {}
        price = float(info.get("last_price") or info.get("lastPrice") or 0) or None
        if price is None:
            # フォールバック（downloadの最後のClose）
            df = yf.download(t, period="5d", interval="1d", auto_adjust=True, progress=False)
            if df is not None and not df.empty:
                price = float(df["Close"].dropna().iloc[-1])
        if price is not None:
            _CACHE[t] = (price, now)
        return price
    except Exception:
        return None