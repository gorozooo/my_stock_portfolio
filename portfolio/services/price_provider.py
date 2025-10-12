# portfolio/services/price_provider.py
from __future__ import annotations
from typing import Dict, Iterable
import time

# 5分キャッシュの超軽量実装
_CACHE: Dict[str, float] = {}
_CACHE_TS: float = 0.0
_CACHE_TTL_SEC = 300

def _use_cache() -> bool:
    return (time.time() - _CACHE_TS) < _CACHE_TTL_SEC

def get_prices(tickers: Iterable[str]) -> Dict[str, float]:
    """
    可能なら yfinance で現在値を取得。失敗/未インストールなら空辞書を返す。
    呼び出し側で avg_cost フォールバックする設計。
    """
    global _CACHE_TS, _CACHE
    tickers = [t.strip().upper() for t in tickers if t]
    if not tickers:
        return {}

    # 5分キャッシュ
    if _use_cache():
        have_all = all(t in _CACHE for t in tickers)
        if have_all:
            return {t: _CACHE[t] for t in tickers if t in _CACHE}

    try:
        import yfinance as yf  # type: ignore
    except Exception:
        return {}

    try:
        data = yf.download(" ".join(tickers), period="1d", interval="1m", progress=False)
        # yfinance は銘柄数で DataFrame 形状が変わるので吸収
        out: Dict[str, float] = {}
        if len(tickers) == 1:
            t = tickers[0]
            try:
                last = float(data["Close"].dropna().iloc[-1])
                out[t] = last
            except Exception:
                pass
        else:
            # カラムは MultiIndex ("Close", "TICKER")
            if ("Close" in data.columns):
                # 単独の"Close"列ケースもあるので上の分岐に倣う
                try:
                    last_all = data["Close"].tail(1)
                    for t in tickers:
                        try:
                            val = float(last_all[t].values[-1])
                            out[t] = val
                        except Exception:
                            pass
                except Exception:
                    pass
            else:
                try:
                    last_row = data.tail(1)
                    for t in tickers:
                        try:
                            val = float(last_row["Close"][t].values[-1])
                            out[t] = val
                        except Exception:
                            pass
                except Exception:
                    pass

        if out:
            for k, v in out.items():
                _CACHE[k] = v
            _CACHE_TS = time.time()
        return out
    except Exception:
        return {}