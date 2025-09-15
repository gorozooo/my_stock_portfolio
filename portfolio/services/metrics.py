# portfolio/services/metrics.py
from __future__ import annotations
import re
import numpy as np
import pandas as pd
import yfinance as yf

TRADING_DAYS = 252

# ---- ティッカー正規化（4〜5桁は .T 付与） ----
_JP_ALNUM = re.compile(r"^[0-9A-Z]{4,5}$")
def _normalize_ticker(raw: str) -> str:
    t = (raw or "").strip().upper()
    if not t: return t
    if "." in t: return t
    return f"{t}.T" if _JP_ALNUM.match(t) else t

# ---- yfinance の列ゆらぎ/MultiIndex を吸収して 1D Series を返す ----
def _get_series(df: pd.DataFrame, field: str) -> pd.Series:
    """
    field: 'Close'|'Adj Close'|'High'|'Low'|'Open'|'Volume'
    - 単一列ならそのまま
    - MultiIndex(level0==field) なら先頭列を使用
    - 数値化してNaN除去
    """
    s = None
    if isinstance(df.columns, pd.MultiIndex):
        # level=0 に field がある？
        lv0 = df.columns.get_level_values(0)
        if field in lv0:
            obj = df.xs(field, axis=1, level=0, drop_level=True)
            s = obj.iloc[:, 0] if isinstance(obj, pd.DataFrame) else obj
    else:
        if field in df.columns:
            s = df[field]

    # Close/Adj Close の代替
    if s is None and field == "Close" and "Adj Close" in (df.columns if not isinstance(df.columns, pd.MultiIndex) else []):
        s = df["Adj Close"]

    if s is None:
        raise KeyError(field)

    if isinstance(s, pd.DataFrame):
        if s.shape[1] == 0:
            raise KeyError(field)
        s = s.iloc[:, 0]

    return pd.to_numeric(s, errors="coerce").dropna()

def _get_close(df: pd.DataFrame) -> pd.Series:
    try:
        return _get_series(df, "Close")
    except KeyError:
        return _get_series(df, "Adj Close")

# ---- 指標ユーティリティ ----
def _ann_vol(ret: pd.Series) -> float:
    return float(ret.std() * np.sqrt(TRADING_DAYS) * 100.0)

def _adx_df(h: pd.Series, l: pd.Series, c: pd.Series, n: int = 14) -> float:
    up = h.diff(); dn = -l.diff()
    plus_dm  = np.where((up > dn) & (up > 0), up, 0.0)
    minus_dm = np.where((dn > up) & (dn > 0), dn, 0.0)
    plus_dm  = pd.Series(plus_dm, index=h.index)
    minus_dm = pd.Series(minus_dm, index=h.index)

    tr = pd.concat([(h - l).abs(), (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    alpha = 1 / n
    atr = tr.ewm(alpha=alpha, adjust=False).mean()
    plus_di  = 100 * plus_dm.ewm(alpha=alpha, adjust=False).mean()  / atr
    minus_di = 100 * minus_dm.ewm(alpha=alpha, adjust=False).mean() / atr
    dx  = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
    adx = dx.ewm(alpha=alpha, adjust=False).mean()
    return float(adx.dropna().iloc[-1])

def _download_with_fallback(ticker: str, days: int) -> pd.DataFrame | None:
    df = yf.download(ticker, period=f"{days}d", interval="1d", progress=False)
    if df is None or df.empty:
        return None
    return df

def get_metrics(ticker: str, bench: str = "^TOPX", days: int = 420) -> dict:
    ticker = _normalize_ticker(ticker)

    # --- 対象銘柄 ---
    df = _download_with_fallback(ticker, days)
    if df is None:
        raise ValueError("no data")

    s = _get_close(df)
    ret = s.pct_change()

    # --- ベンチ（フォールバック: 指定 → ^N225 → ^GSPC） ---
    b = None
    for cand in [bench, "^N225", "^GSPC"]:
        try:
            bdf = _download_with_fallback(cand, days)
            if bdf is not None:
                b = _get_close(bdf)
                break
        except Exception:
            continue
    bret = b.pct_change() if b is not None else None

    # --- トレンド：回帰傾き（60日・年率） ---
    y = s.tail(60).to_numpy(dtype=float).ravel()   # ← 常に 1 次元
    if len(y) < 2:
        raise ValueError("insufficient data for regression")
    x = np.arange(len(y), dtype=float)
    k, _ = np.polyfit(x, y, 1)
    slope_ann_pct = float((k / y[-1]) * 100.0 * TRADING_DAYS)

    ma20  = float(s.rolling(20).mean().iloc[-1])  if len(s) >= 20  else None
    ma50  = float(s.rolling(50).mean().iloc[-1])  if len(s) >= 50  else None
    ma200 = float(s.rolling(200).mean().iloc[-1]) if len(s) >= 200 else None
    ma_stack = (
        "bull" if (ma20 is not None and ma50 is not None and ma200 is not None and ma20 > ma50 > ma200)
        else "bear" if (ma20 is not None and ma50 is not None and ma200 is not None and ma20 < ma50 < ma200)
        else "mixed"
    )

    # --- ADX(14) ---
    # 必要列を 1D Series で取得し、共通インデックスに合わせる
    h = _get_series(df, "High"); l = _get_series(df, "Low"); c = _get_close(df)
    common = s.index.intersection(h.index).intersection(l.index).intersection(c.index)
    adx14 = _adx_df(h.reindex(common), l.reindex(common), c.reindex(common), n=14)

    # --- 相対強さ（6か月, 126 営業日） ---
    rs_6m = None
    if bret is not None and len(s) > 126 and len(b) > 126:
        try:
            rs_6m = float((s.pct_change(126).iloc[-1] - b.pct_change(126).iloc[-1]) * 100)
        except Exception:
            rs_6m = None

    # --- 52週高値/安値乖離 ---
    from_52w_high = from_52w_low = None
    if len(s) >= 252:
        roll_max = float(s.rolling(252).max().iloc[-1])
        roll_min = float(s.rolling(252).min().iloc[-1])
        last = float(s.iloc[-1])
        from_52w_high = float((last / roll_max - 1) * 100) if roll_max else None
        from_52w_low  = float((last / roll_min - 1) * 100) if roll_min else None

    # --- リスク・流動性 ---
    vol20 = _ann_vol(ret.tail(20).dropna()) if len(ret.dropna()) >= 20 else None
    vol60 = _ann_vol(ret.tail(60).dropna()) if len(ret.dropna()) >= 60 else None

    # ATR(14)
    tr = pd.concat([
        (h - l).abs(),
        (h - c.shift()).abs(),
        (l - c.shift()).abs()
    ], axis=1).max(axis=1)
    atr14 = float(tr.rolling(14).mean().iloc[-1])

    # ADV20 = (Close * Volume) の20日平均
    adv20 = None
    try:
        v = _get_series(df, "Volume").reindex(s.index)   # ← 1D numeric Series
        adv20 = float((s * v).rolling(20).mean().iloc[-1])
    except Exception:
        adv20 = None

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
        "liquidity": {
            "adv20": adv20
        }
    }