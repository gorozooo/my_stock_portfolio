# portfolio/services/price_provider.py
from __future__ import annotations
from typing import Dict, Iterable, List, Tuple
import time
from statistics import median

from django.db.models import Max
from portfolio.models import RealizedTrade  # 直近約定のフォールバックに使用

# 5分キャッシュ
_CACHE: Dict[str, float] = {}
_CACHE_TS: float = 0.0
_CACHE_TTL = 300


def _cache_valid() -> bool:
    return (time.time() - _CACHE_TS) < _CACHE_TTL


def _to_vendor_symbol(ticker: str) -> str:
    """
    ティッカーをデータ供給元(yfinance)の記法に正規化。
    - 東証銘柄: 4桁数字 → '.T' を付与（例: '7013' -> '7013.T'）
    - すでにサフィックス付き / 英字中心のUS銘柄はそのまま
    """
    t = (ticker or "").strip().upper()
    if not t:
        return t
    if "." in t:
        return t
    if t.isdigit() and (3 <= len(t) <= 5):
        return f"{t}.T"
    return t


def _fallback_from_trades(tickers: List[str]) -> Dict[str, float]:
    """
    RealizedTrade から各ティッカーの直近日の約定価格「中央値」を推定現在値として返す。
    """
    out: Dict[str, float] = {}
    if not tickers:
        return out

    latest = (
        RealizedTrade.objects.filter(ticker__in=tickers)
        .values("ticker")
        .annotate(lat=Max("trade_at"))
    )
    lat_map = {r["ticker"].upper(): r["lat"] for r in latest if r["lat"]}
    if not lat_map:
        return out

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
    価格取得の多段ロジック：
      1) yfinance（JPは自動で .T を付与）
      2) 直近約定の中央値（RealizedTrade）
      3) 返らないものは呼び出し側で avg_cost にフォールバック
    戻り値は「元のティッカー名 → 価格」マップ。
    """
    global _CACHE_TS, _CACHE
    origs = [(t or "").strip().upper() for t in tickers_in if t]
    base_tickers = sorted(set(origs))
    if not base_tickers:
        return {}

    # キャッシュ命中なら即返す
    if _cache_valid():
        had = {t: _CACHE[t] for t in base_tickers if t in _CACHE}
        if len(had) == len(base_tickers):
            return had

    # 1) yfinance（ベンダー記法に変換し、取得後は元ティッカーへ戻す）
    out: Dict[str, float] = {}
    try:
        import yfinance as yf  # type: ignore

        mapping: Dict[str, str] = {t: _to_vendor_symbol(t) for t in base_tickers}
        vendor_syms = sorted(set(mapping.values()))
        data = yf.download(" ".join(vendor_syms), period="1d", interval="1m", progress=False)

        if hasattr(data, "empty") and not data.empty:
            try:
                last = data.tail(1)
                # 形状1: 単一Close列（単一銘柄など）
                if "Close" in last.columns and not isinstance(last.columns, tuple):
                    # 単一銘柄ケース
                    try:
                        v = float(data["Close"].dropna().iloc[-1])
                        # vendor_syms[0] を逆引き
                        for orig, vend in mapping.items():
                            if vend == vendor_syms[0] and v > 0:
                                out[orig] = v
                    except Exception:
                        pass
                else:
                    # 形状2: MultiIndex ("Close", "SYMBOL")
                    for orig, vend in mapping.items():
                        try:
                            v = float(last["Close"][vend].values[-1])
                            if v > 0:
                                out[orig] = v
                        except Exception:
                            pass
            except Exception:
                pass
    except Exception:
        # yfinance 未導入/失敗は無視
        pass

    # 2) 直近約定の中央値で補完
    missing = [t for t in base_tickers if t not in out]
    if missing:
        out.update(_fallback_from_trades(missing))

    # キャッシュ保存（取れた分だけ）
    if out:
        for k, v in out.items():
            _CACHE[k] = v
        _CACHE_TS = time.time()

    return out