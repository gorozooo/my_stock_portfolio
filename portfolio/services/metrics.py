from __future__ import annotations
import numpy as np
import pandas as pd
import yfinance as yf

TRADING_DAYS = 252

def _get_close(df: pd.DataFrame) -> pd.Series:
    if "Close" in df.columns:
        s = df["Close"]
    elif "Adj Close" in df.columns:
        s = df["Adj Close"]
    else:
        for c in df.columns:
            if isinstance(c, tuple) and c[0] in ("Close", "Adj Close"):
                s = df[c]
                break
        else:
            raise KeyError("Close")
    return pd.to_numeric(s, errors="coerce").dropna()

def _ann_vol(ret: pd.Series) -> float:
    return float(ret.std() * np.sqrt(TRADING_DAYS) * 100.0)

def _adx(df: pd.DataFrame, n: int = 14) -> float:
    h = pd.to_numeric(df["High"], errors="coerce")
    l = pd.to_numeric(df["Low"],  errors="coerce")
    c = pd.to_numeric(df["Close"], errors="coerce")
    up = h.diff()
    dn = -l.diff()
    plus_dm  = pd.Series(np.where((up > dn) & (up > 0), up, 0.0),  index=h.index, dtype=float)
    minus_dm = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), index=h.index, dtype=float)

    tr = pd.concat([
        (h - l).abs(),
        (h - c.shift()).abs(),
        (l - c.shift()).abs()
    ], axis=1).max(axis=1)

    atr = tr.ewm(alpha=1/n, adjust=False).mean()
    plus_di  = 100 * plus_dm.ewm(alpha=1/n, adjust=False).mean()  / atr
    minus_di = 100 * minus_dm.ewm(alpha=1/n, adjust=False).mean() / atr
    dx  = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
    adx = dx.ewm(alpha=1/n, adjust=False).mean()
    return float(adx.dropna().iloc[-1])

def get_metrics(ticker: str, bench: str = "^TOPX", days: int = 420) -> dict:
    df = yf.download(ticker, period=f"{days}d", interval="1d", progress=False)
    if df is None or df.empty:
        raise ValueError("no data")

    use_cols: dict[str, pd.Series] = {}
    for col in ("Open", "High", "Low", "Close", "Adj Close", "Volume"):
        if col in df.columns:
            use_cols[col] = pd.to_numeric(df[col], errors="coerce")
    dfx = pd.DataFrame(use_cols).dropna(how="all")
    if "Close" not in dfx.columns and "Adj Close" in dfx.columns:
        dfx["Close"] = dfx["Adj Close"]

    if not {"High", "Low", "Close"}.issubset(dfx.columns):
        raise ValueError("no aligned data")

    s = _get_close(dfx)
    ret = s.pct_change()

    try:
        bdf = yf.download(bench, period=f"{days}d", interval="1d", progress=False)
        b = _get_close(bdf)
        bret = b.pct_change()
    except Exception:
        b = None
        bret = None

    # ★ 1 次元に強制してから polyfit
    y = np.ravel(s.tail(60).to_numpy(dtype=float))
    x = np.arange(len(y), dtype=float)
    if len(y) < 2:
        raise ValueError("not enough data for regression")
    k, _ = np.polyfit(x, y, 1)
    slope_ann_pct = float((k / y[-1]) * 100.0 * TRADING_DAYS)

    ma20  = float(s.rolling(20).mean().iloc[-1])   if len(s) >= 20  else None
    ma50  = float(s.rolling(50).mean().iloc[-1])   if len(s) >= 50  else None
    ma200 = float(s.rolling(200).mean().iloc[-1])  if len(s) >= 200 else None
    ma_stack = (
        "bull" if (ma20 is not None and ma50 is not None and ma200 is not None and ma20 > ma50 > ma200)
        else ("bear" if (ma20 is not None and ma50 is not None and ma200 is not None and ma20 < ma50 < ma200)
              else "mixed")
    )

    adx14 = _adx(dfx.dropna()[["High", "Low", "Close"]])

    rs_6m = None
    if bret is not None and len(s) > 126 and len(b) > 126:
        rs_6m = float((s.pct_change(126).iloc[-1] - b.pct_change(126).iloc[-1]) * 100)

    roll_max = s.rolling(252).max().iloc[-1] if len(s) >= 252 else None
    roll_min = s.rolling(252).min().iloc[-1] if len(s) >= 252 else None
    from_52w_high = float((s.iloc[-1] / roll_max - 1) * 100) if roll_max else None
    from_52w_low  = float((s.iloc[-1] / roll_min - 1) * 100) if roll_min else None

    vol20 = _ann_vol(ret.tail(20).dropna()) if len(ret.dropna()) >= 20 else None
    vol60 = _ann_vol(ret.tail(60).dropna()) if len(ret.dropna()) >= 60 else None

    tr = pd.concat([
        (dfx["High"] - dfx["Low"]).abs(),
        (dfx["High"] - dfx["Close"].shift()).abs(),
        (dfx["Low"]  - dfx["Close"].shift()).abs()
    ], axis=1).max(axis=1)
    atr14 = float(tr.rolling(14).mean().iloc[-1]) if len(tr.dropna()) >= 14 else None

    adv20 = float((s * dfx["Volume"]).rolling(20).mean().iloc[-1]) if "Volume" in dfx.columns else None

    swing_win   = 20
    swing_high  = float(dfx["High"].tail(swing_win).max())
    swing_low   = float(dfx["Low"].tail(swing_win).min())
    last_close  = float(s.iloc[-1])

    entry_level = None
    stop_level  = None
    if atr14 is not None:
        entry_level = float(swing_high + 0.5 * atr14)
        stop_level  = float(swing_low  - 1.5 * atr14)

    return {
        "ok": True,
        "asof": str(s.index[-1].date()),
        "trend": {
            "slope_ann_pct_60": slope_ann_pct,
            "ma": {"20": ma20, "50": ma50, "200": ma200, "stack": ma_stack},
            "adx14": adx14,
        },
        "relative": {
            "rs_6m_pct": rs_6m,
            "from_52w_high_pct": from_52w_high,
            "from_52w_low_pct":  from_52w_low,
        },
        "risk": {
            "vol20_ann_pct": vol20,
            "vol60_ann_pct": vol60,
            "atr14": atr14,
        },
        "liquidity": {
            "adv20": adv20,
        },
        "levels": {
            "entry": entry_level,
            "stop":  stop_level,
            "swing_high": swing_high,
            "swing_low":  swing_low,
            "last_close": last_close,
            "atr14": atr14,
            "window": swing_win,
        },
    }