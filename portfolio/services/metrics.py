# portfolio/services/metrics.py
from __future__ import annotations
from typing import Optional, Union

import numpy as np
import pandas as pd
import yfinance as yf

TRADING_DAYS = 252


# ---------- 共通: 安全に float へ落とす ----------
def _to_float(v: Union[pd.Series, np.ndarray, float, int, None]) -> Optional[float]:
    """Series/ndarray/np.number を float か None に正規化"""
    try:
        if v is None:
            return None
        if isinstance(v, pd.Series):
            if v.empty:
                return None
            v = v.iloc[-1]
        if isinstance(v, np.ndarray):
            if v.size == 0:
                return None
            v = v.reshape(-1)[-1]
        if pd.isna(v):
            return None
        return float(v)
    except Exception:
        return None


# ---------- Close 抽出（yfinance の列ゆらぎ対応） ----------
def _get_close(df: pd.DataFrame) -> pd.Series:
    """yfinanceの列ゆらぎを吸収してClose系列を返す（auto_adjust=True想定）"""
    if "Close" in df.columns:
        s = df["Close"]
    elif "Adj Close" in df.columns:
        s = df["Adj Close"]
    else:
        # マルチインデックス対策
        s = None
        for c in df.columns:
            if isinstance(c, tuple) and c[0] in ("Close", "Adj Close"):
                s = df[c]
                break
        if s is None:
            raise KeyError("Close")
    return pd.to_numeric(s, errors="coerce").dropna()


def _ann_vol(ret: pd.Series) -> Optional[float]:
    ret = ret.dropna()
    if ret.empty:
        return None
    return float(ret.std() * np.sqrt(TRADING_DAYS) * 100.0)


# ---------- ADX（Wilder 簡易） ----------
def _adx(df: pd.DataFrame, n: int = 14) -> Optional[float]:
    """Wilder法に準じた簡易ADX（dfはHigh/Low/Close必須）"""
    try:
        h = pd.to_numeric(df["High"], errors="coerce")
        l = pd.to_numeric(df["Low"], errors="coerce")
        c = pd.to_numeric(df["Close"], errors="coerce")
        up = h.diff()
        dn = -l.diff()
        plus_dm = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), index=h.index)
        minus_dm = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), index=h.index)

        tr = pd.concat(
            [(h - l).abs(), (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1
        ).max(axis=1)

        # Wilder smoothing
        alpha = 1.0 / n
        atr = tr.ewm(alpha=alpha, adjust=False).mean()
        plus_di = 100 * plus_dm.ewm(alpha=alpha, adjust=False).mean() / atr
        minus_di = 100 * minus_dm.ewm(alpha=alpha, adjust=False).mean() / atr
        dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
        adx = dx.ewm(alpha=alpha, adjust=False).mean()
        return _to_float(adx.dropna())
    except Exception:
        return None


def get_metrics(ticker: str, bench: str = "^TOPX", days: int = 420) -> dict:
    # ---- 価格（数値化・欠損落とし）----
    df = yf.download(ticker, period=f"{days}d", interval="1d", progress=False)
    if df is None or df.empty:
        raise ValueError("no data")

    use_cols = {}
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

    # ---- ベンチ ----
    try:
        bdf = yf.download(bench, period=f"{days}d", interval="1d", progress=False)
        b = _get_close(bdf)
        bret = b.pct_change()
    except Exception:
        b = None
        bret = None

    # ---- トレンド：回帰傾き（60日・年率）----
    y = s.tail(60).to_numpy(dtype=float).reshape(-1)
    if y.size < 2:
        raise ValueError("not enough data for regression")
    x = np.arange(y.size, dtype=float)
    k, _ = np.polyfit(x, y, 1)
    slope_ann_pct = float((k / y[-1]) * 100.0 * TRADING_DAYS)

    ma20 = _to_float(s.rolling(20).mean())
    ma50 = _to_float(s.rolling(50).mean())
    ma200 = _to_float(s.rolling(200).mean())
    ma_stack = (
        "bull"
        if (ma20 is not None and ma50 is not None and ma200 is not None and ma20 > ma50 > ma200)
        else (
            "bear"
            if (ma20 is not None and ma50 is not None and ma200 is not None and ma20 < ma50 < ma200)
            else "mixed"
        )
    )

    # ---- ADX(14) ----
    adx14 = _adx(dfx[["High", "Low", "Close"]].dropna())

    # ---- 相対強さ：6か月超過（~126営）----
    rs_6m = None
    if bret is not None and len(s) > 126 and b is not None and len(b) > 126:
        rs_6m = float((s.pct_change(126).iloc[-1] - b.pct_change(126).iloc[-1]) * 100)

    # ---- 52週高値/安値乖離 ----
    if len(s) >= 252:
        roll_max = float(s.tail(252).max())
        roll_min = float(s.tail(252).min())
        last = float(s.iloc[-1])
        from_52w_high = float((last / roll_max - 1) * 100)
        from_52w_low = float((last / roll_min - 1) * 100)
    else:
        from_52w_high = None
        from_52w_low = None

    # ---- リスク・流動性 ----
    vol20 = _ann_vol(ret.tail(20))
    vol60 = _ann_vol(ret.tail(60))

    tr = pd.concat(
        [
            (dfx["High"] - dfx["Low"]).abs(),
            (dfx["High"] - dfx["Close"].shift()).abs(),
            (dfx["Low"] - dfx["Close"].shift()).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr14 = _to_float(tr.rolling(14).mean())

    adv20 = None
    if "Volume" in dfx.columns:
        # index を合わせて売買代金を20日平均
        adv20 = _to_float((s * dfx["Volume"]).rolling(20).mean())

    # ---- プロ仕様：エントリー/損切り指針（ATRベース）----
    swing_win = 20
    swing_high = _to_float(dfx["High"].tail(swing_win).max())
    swing_low = _to_float(dfx["Low"].tail(swing_win).min())
    last_close = _to_float(s.iloc[-1])

    entry_level = None
    stop_level = None
    if atr14 is not None and swing_high is not None and swing_low is not None:
        entry_level = float(swing_high + 0.5 * atr14)
        stop_level = float(swing_low - 1.5 * atr14)

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
            "from_52w_low_pct": from_52w_low,
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
            "stop": stop_level,
            "swing_high": swing_high,
            "swing_low": swing_low,
            "last_close": last_close,
            "atr14": atr14,
            "window": swing_win,
        },
    }