# portfolio/services/price_provider.py
from __future__ import annotations
from typing import Dict, Iterable, List
import time
from statistics import median

from django.db.models import Max
from portfolio.models import RealizedTrade  # 直近約定のフォールバックに使用

# 5分キャッシュ（超軽量）
_CACHE: Dict[str, float] = {}
_CACHE_TS: float = 0.0
_CACHE_TTL = 300


def _cache_valid() -> bool:
    return (time.time() - _CACHE_TS) < _CACHE_TTL


def _fallback_from_trades(tickers: List[str]) -> Dict[str, float]:
    """
    RealizedTrade から各ティッカーの「直近N件の約定価格の中央値」を推定現在値として返す。
    “たまたま同値”による含み損益0固定を避けるため、中央値を使う。
    """
    out: Dict[str, float] = {}
    if not tickers:
        return out

    # まず各tickerの最新日付を取る（軽量化）
    latest = (
        RealizedTrade.objects.filter(ticker__in=tickers)
        .values("ticker")
        .annotate(lat=Max("trade_at"))
    )
    lat_map = {r["ticker"].upper(): r["lat"] for r in latest if r["lat"]}

    if not lat_map:
        return out

    # 最新日の複数行（同日売買など）を拾って中央値化
    rows = (
        RealizedTrade.objects
        .filter(ticker__in=list(lat_map.keys()), trade_at__in=list(lat_map.values()))
        .values("ticker", "price")
    )
    buf: Dict[str, List[float]] = {}
    for r in rows:
        t = (r["ticker"] or "").upper()
        try:
            buf.setdefault(t, []).append(float(r["price"]))
        except Exception:
            pass

    for t, arr in buf.items():
        try:
            out[t] = float(median(arr))
        except Exception:
            pass

    return out


def get_prices(tickers_in: Iterable[str]) -> Dict[str, float]:
    """
    可能なら yfinance、なければ RealizedTrade の中央値で補完。
    返らない銘柄は呼び出し側で avg_cost にフォールバックする設計。
    """
    global _CACHE_TS, _CACHE
    tickers = sorted({(t or "").strip().upper() for t in tickers_in if t})
    if not tickers:
        return {}

    # キャッシュ
    if _cache_valid():
        had = {t: _CACHE[t] for t in tickers if t in _CACHE}
        if len(had) == len(tickers):
            return had

    out: Dict[str, float] = {}

    # 1) yfinance
    try:
        import yfinance as yf  # type: ignore

        data = yf.download(" ".join(tickers), period="1d", interval="1m", progress=False)
        if hasattr(data, "empty") and not data.empty:
            try:
                last = data.tail(1)
                if "Close" in last.columns:  # 形状1
                    row = last["Close"]
                    for t in tickers:
                        try:
                            v = float(row[t].values[-1])
                            if v > 0:
                                out[t] = v
                        except Exception:
                            pass
                else:  # 形状2（MultiIndex）
                    for t in tickers:
                        try:
                            v = float(last["Close"][t].values[-1])
                            if v > 0:
                                out[t] = v
                        except Exception:
                            pass
            except Exception:
                # 単一銘柄形状
                try:
                    v = float(data["Close"].dropna().iloc[-1])
                    if v > 0:
                        out[tickers[0]] = v
                except Exception:
                    pass
    except Exception:
        pass  # yfinance未導入/失敗は無視

    # 2) 直近約定の中央値で補完
    missing = [t for t in tickers if t not in out]
    if missing:
        out.update(_fallback_from_trades(missing))

    # キャッシュ保存
    if out:
        for k, v in out.items():
            _CACHE[k] = v
        _CACHE_TS = time.time()

    return out