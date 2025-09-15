# portfolio/services/metrics.py
from __future__ import annotations
import numpy as np
import pandas as pd
import yfinance as yf

TRADING_DAYS = 252


# ---------- 共通ユーティリティ ----------
def _first(series_like):
    """Series/DataFrame/ndarray から最後の有効値を float で返す（なければ None）"""
    try:
        if isinstance(series_like, (pd.Series, pd.Index)):
            v = series_like.dropna().iloc[-1]
        elif isinstance(series_like, pd.DataFrame):
            v = series_like.iloc[:, 0].dropna().iloc[-1]
        else:
            return None
        return float(v)
    except Exception:
        return None


def _get_col(df: pd.DataFrame, key: str) -> pd.Series | None:
    """
    yfinance の列ゆらぎを吸収して 1列の Series を返す。
    - 大小文字無視
    - 単一列 or MultiIndex(level=0) のどちらでも可
    - 見つからない場合は None
    """
    if df is None or df.empty:
        return None

    # 単一インデックス
    for c in df.columns:
        if isinstance(c, str) and c.lower() == key.lower():
            return pd.to_numeric(df[c], errors="coerce")

    # MultiIndex(level=0=OHLCV, level=1=ticker)
    if isinstance(df.columns, pd.MultiIndex):
        lvl0 = [c[0].lower() if isinstance(c, tuple) else str(c).lower()
                for c in df.columns]
        if key.lower() in lvl0:
            s = df.xs(key, axis=1, level=0, drop_level=True)
            # 複数ティッカーが来た場合は先頭列を採用
            if isinstance(s, pd.DataFrame):
                s = s.iloc[:, 0]
            return pd.to_numeric(s, errors="coerce")

    # 代替（Close が無い場合 Adj Close を Close とみなす等）
    if key.lower() == "close":
        alt = _get_col(df, "Adj Close")
        if alt is not None:
            return alt

    return None


def _get_close(df: pd.DataFrame) -> pd.Series:
    s = _get_col(df, "Close")
    if s is None:
        raise KeyError("Close")
    return s.dropna()


def _get_high(df: pd.DataFrame) -> pd.Series | None:
    s = _get_col(df, "High")
    return s.dropna() if s is not None else None


def _get_low(df: pd.DataFrame) -> pd.Series | None:
    s = _get_col(df, "Low")
    return s.dropna() if s is not None else None


def _get_volume(df: pd.DataFrame) -> pd.Series | None:
    s = _get_col(df, "Volume")
    return s.dropna() if s is not None else None


def _ann_vol(ret: pd.Series | None) -> float | None:
    if ret is None:
        return None
    ret = ret.dropna()
    if len(ret) < 2:
        return None
    return float(ret.std() * np.sqrt(TRADING_DAYS) * 100.0)


# ---------- ADX（Wilder 法に準拠・簡易） ----------
def _adx_from_hlc(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 14) -> float | None:
    try:
        # インデックスを揃える
        df = pd.concat([high, low, close], axis=1, join="inner")
        df.columns = ["H", "L", "C"]
        df = df.dropna()
        if len(df) < n + 2:
            return None

        h, l, c = df["H"], df["L"], df["C"]
        up = h.diff()
        dn = -l.diff()
        plus_dm = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), index=h.index)
        minus_dm = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), index=h.index)

        tr = pd.concat([
            (h - l).abs(),
            (h - c.shift()).abs(),
            (l - c.shift()).abs()
        ], axis=1).max(axis=1)

        # Wilder 平滑（EMA で近似）
        alpha = 1 / n
        atr = tr.ewm(alpha=alpha, adjust=False).mean()
        plus_di = 100 * plus_dm.ewm(alpha=alpha, adjust=False).mean() / atr
        minus_di = 100 * minus_dm.ewm(alpha=alpha, adjust=False).mean() / atr
        dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
        adx = dx.ewm(alpha=alpha, adjust=False).mean()
        return float(adx.dropna().iloc[-1])
    except Exception:
        return None


# ---------- メイン ----------
def get_metrics(ticker: str, bench: str = "^TOPX", days: int = 420) -> dict:
    # 価格
    df = yf.download(str(ticker), period=f"{days}d", interval="1d", progress=False)
    if df is None or df.empty:
        raise ValueError("no data")

    s = _get_close(df)
    s = pd.to_numeric(s, errors="coerce").dropna()
    if s.empty:
        raise ValueError("no close data")

    ret = s.pct_change()

    # ベンチ（失敗しても続行）
    b, bret = None, None
    try:
        bdf = yf.download(str(bench), period=f"{days}d", interval="1d", progress=False)
        if bdf is not None and not bdf.empty:
            b = _get_close(bdf)
            b = pd.to_numeric(b, errors="coerce").dropna()
            bret = b.pct_change()
    except Exception:
        b, bret = None, None

    # --- トレンド：回帰傾き（60日・年率） ---
    y = s.tail(60).to_numpy(dtype=float).ravel()   # 1次元に整形
    slope_ann_pct = None
    if len(y) >= 2:
        x = np.arange(len(y), dtype=float)
        k, _ = np.polyfit(x, y, 1)
        slope_ann_pct = float((k / y[-1]) * 100.0 * TRADING_DAYS)

    # --- MA 20/50/200 ---
    ma20 = _first(s.rolling(20).mean())
    ma50 = _first(s.rolling(50).mean())
    ma200 = _first(s.rolling(200).mean())
    if all(v is not None for v in (ma20, ma50, ma200)):
        ma_stack = "bull" if (ma20 > ma50 > ma200) else ("bear" if (ma20 < ma50 < ma200) else "mixed")
    else:
        ma_stack = "mixed"

    # --- ADX(14) ---
    high = _get_high(df)
    low = _get_low(df)
    adx14 = _adx_from_hlc(high, low, s) if (high is not None and low is not None) else None

    # --- 相対強さ: 6か月（126営業日）の超過 ---
    rs_6m = None
    if b is not None and len(s) > 126 and len(b) > 126:
        # 同一営業日のリターンに揃えて積み上げ
        r = pd.concat([s.pct_change(), b.pct_change()], axis=1, join="inner").dropna()
        r.columns = ["s", "b"]
        r6 = r.tail(126)
        if len(r6) >= 2:
            cum_s = (1 + r6["s"]).prod() - 1.0
            cum_b = (1 + r6["b"]).prod() - 1.0
            rs_6m = float((cum_s - cum_b) * 100.0)

    # --- 52週高値/安値 乖離 ---
    from_52w_high = from_52w_low = None
    if len(s) >= 252:
        roll_max = float(s.tail(252).max())
        roll_min = float(s.tail(252).min())
        last = float(s.iloc[-1])
        if roll_max:
            from_52w_high = float((last / roll_max - 1) * 100.0)
        if roll_min:
            from_52w_low = float((last / roll_min - 1) * 100.0)

    # --- リスク（年化ボラ/ATR） ---
    vol20 = _ann_vol(ret.tail(20))
    vol60 = _ann_vol(ret.tail(60))

    atr14 = None
    if high is not None and low is not None:
        tr = pd.concat([
            (high - low).abs(),
            (high - s.shift()).abs(),
            (low - s.shift()).abs()
        ], axis=1).max(axis=1)
        atr14 = _first(tr.rolling(14).mean())

    # --- 流動性（ADV20） ---
    volume = _get_volume(df)
    adv20 = None
    if volume is not None:
        adv20 = _first((s * volume).rolling(20).mean())

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
    }