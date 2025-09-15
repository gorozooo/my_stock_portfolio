# portfolio/services/metrics.py
from __future__ import annotations
import numpy as np
import pandas as pd
import yfinance as yf

TRADING_DAYS = 252

def _get_close(df: pd.DataFrame) -> pd.Series:
    """yfinanceの列ゆらぎを吸収してClose系列を返す（必ず Series で返す）"""
    s = None
    if "Close" in df.columns:
        s = df["Close"]
    elif "Adj Close" in df.columns:
        s = df["Adj Close"]
    else:
        # マルチインデックス/タプル列対策
        for c in df.columns:
            if isinstance(c, tuple) and c[0] in ("Close", "Adj Close"):
                s = df[c]
                break
        if s is None:
            raise KeyError("Close")
    # DataFrame で返ってきたら 1列目を Series に絞る
    if isinstance(s, pd.DataFrame):
        s = s.iloc[:, 0]
    return pd.to_numeric(s, errors="coerce").dropna()

def _ann_vol(ret: pd.Series) -> float:
    return float(ret.std() * np.sqrt(TRADING_DAYS) * 100.0)

def _adx(df: pd.DataFrame, n: int = 14) -> float:
    """Wilder法の簡易ADX（dfは High/Low/Close 必須）"""
    h, l, c = df["High"], df["Low"], df["Close"]
    up = h.diff(); dn = -l.diff()
    plus_dm  = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), index=h.index)
    minus_dm = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), index=h.index)
    tr = pd.concat([(h - l).abs(), (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1/n, adjust=False).mean()
    plus_di  = 100 * plus_dm.ewm(alpha=1/n, adjust=False).mean() / atr
    minus_di = 100 * minus_dm.ewm(alpha=1/n, adjust=False).mean() / atr
    dx  = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
    adx = dx.ewm(alpha=1/n, adjust=False).mean()
    return float(adx.dropna().iloc[-1])

def get_metrics(ticker: str, bench: str = "^TOPX", days: int = 420) -> dict:
    # 価格
    df = yf.download(ticker, period=f"{days}d", interval="1d", progress=False)
    if df is None or df.empty:
        raise ValueError("no data")

    s = _get_close(df)
    ret = s.pct_change()

    # ベンチ
    try:
        bdf = yf.download(bench, period=f"{days}d", interval="1d", progress=False)
        b = _get_close(bdf)
        bret = b.pct_change()
    except Exception:
        b = None
        bret = None

    # トレンド：回帰傾き（60日・年率）—— y を 1 次元にするのが超重要
    y = s.tail(60).to_numpy(dtype=float).ravel()
    x = np.arange(len(y), dtype=float)
    k, _ = np.polyfit(x, y, 1)
    slope_ann_pct = float((k / y[-1]) * 100.0 * TRADING_DAYS)

    ma20  = float(s.rolling(20).mean().iloc[-1])  if len(s) >= 20  else None
    ma50  = float(s.rolling(50).mean().iloc[-1])  if len(s) >= 50  else None
    ma200 = float(s.rolling(200).mean().iloc[-1]) if len(s) >= 200 else None
    ma_stack = "bull" if (ma20 is not None and ma50 is not None and ma200 is not None and ma20 > ma50 > ma200) \
        else ("bear" if (ma20 is not None and ma50 is not None and ma200 is not None and ma20 < ma50 < ma200) else "mixed")

    # ADX(14)
    use = df.dropna()[["High","Low","Close"]]
    adx14 = _adx(use)

    # 相対強さ：6か月超過（126営業日）
    rs_6m = None
    if b is not None and len(s) > 126 and len(b) > 126:
        rs_6m = float((s.pct_change(126).iloc[-1] - b.pct_change(126).iloc[-1]) * 100)

    # 52週高値/安値乖離
    roll_max = s.rolling(252).max().iloc[-1] if len(s) >= 252 else None
    roll_min = s.rolling(252).min().iloc[-1] if len(s) >= 252 else None
    from_52w_high = float((s.iloc[-1] / roll_max - 1) * 100) if roll_max else None
    from_52w_low  = float((s.iloc[-1] / roll_min - 1) * 100) if roll_min else None

    # リスク・流動性
    vol20 = _ann_vol(ret.tail(20).dropna())
    vol60 = _ann_vol(ret.tail(60).dropna())

    tr = pd.concat([
        (df["High"] - df["Low"]).abs(),
        (df["High"] - df["Close"].shift()).abs(),
        (df["Low"]  - df["Close"].shift()).abs()
    ], axis=1).max(axis=1)
    atr14 = float(tr.rolling(14).mean().iloc[-1])

    adv20 = float((s * df["Volume"]).rolling(20).mean().iloc[-1]) if "Volume" in df.columns else None

    return {
        "ok": True,
        "asof": str(s.index[-1].date()),
        "trend": {
            "slope_ann_pct_60": slope_ann_pct,
            "ma": {"20": ma20, "50": ma50, "200": ma200, "stack": ma_stack},
            "adx14": adx14
        },
        "relative": {
            "rs_6m_pct": rs_6m,
            "from_52w_high_pct": from_52w_high,
            "from_52w_low_pct":  from_52w_low
        },
        "risk": {
            "vol20_ann_pct": vol20,
            "vol60_ann_pct": vol60,
            "atr14": atr14
        },
        "liquidity": { "adv20": adv20 }
    }