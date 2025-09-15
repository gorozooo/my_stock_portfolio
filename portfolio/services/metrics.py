# portfolio/services/metrics.py
from __future__ import annotations
import numpy as np
import pandas as pd
import yfinance as yf

TRADING_DAYS = 252

def _get_close(df: pd.DataFrame) -> pd.Series:
    """
    yfinanceの列ゆらぎを吸収してClose系列(Series)を返す（auto_adjust=True想定）
    - 単一Index: "Close" 優先、なければ "Adj Close"
    - MultiIndex: level=0 が "Close"/"Adj Close" の列を xs で取り出し、複数列なら先頭
    - 最終的に必ず float の 1 次元 Series を返す
    """
    s = None

    if isinstance(df.columns, pd.MultiIndex):
        # level=0 を見て Close/Adj Close を優先抽出
        lvl0 = df.columns.get_level_values(0)
        target = None
        for cand in ("Close", "Adj Close"):
            if cand in lvl0:
                target = cand
                break
        if target is not None:
            obj = df.xs(target, axis=1, level=0, drop_level=True)
            s = obj.iloc[:, 0] if isinstance(obj, pd.DataFrame) else obj
    else:
        cols = {str(c).lower(): c for c in df.columns}
        if "close" in cols:
            s = df[cols["close"]]
        elif "adj close" in cols:
            s = df[cols["adj close"]]

    if s is None:
        # 最後の保険：最終列を Series 化
        s = df.iloc[:, -1]

    # DataFrame→Series になっていない場合の保険
    if isinstance(s, pd.DataFrame):
        s = s.squeeze("columns")
        if isinstance(s, pd.DataFrame):
            s = s.iloc[:, 0]

    # 数値化 + 欠損除去 + 1D化を保証
    s = pd.to_numeric(s, errors="coerce").dropna()
    s.name = "Close"
    return s

def _ann_vol(ret: pd.Series) -> float:
    return float(ret.std() * np.sqrt(TRADING_DAYS) * 100.0)

def _adx(df: pd.DataFrame, n: int = 14) -> float:
    """Wilder法に準じた簡易ADX（dfはHigh/Low/Close必須）"""
    h, l, c = df["High"], df["Low"], df["Close"]
    up = h.diff()
    dn = -l.diff()
    plus_dm = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), index=h.index)
    minus_dm = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), index=h.index)

    tr = pd.concat([
        (h - l).abs(),
        (h - c.shift()).abs(),
        (l - c.shift()).abs()
    ], axis=1).max(axis=1)

    atr = tr.ewm(alpha=1/n, adjust=False).mean()
    plus_di = 100 * plus_dm.ewm(alpha=1/n, adjust=False).mean() / atr
    minus_di = 100 * minus_dm.ewm(alpha=1/n, adjust=False).mean() / atr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
    adx = dx.ewm(alpha=1/n, adjust=False).mean()
    return float(adx.dropna().iloc[-1])

def get_metrics(ticker: str, bench: str = "^TOPX", days: int = 420) -> dict:
    # 価格
    df = yf.download(ticker, period=f"{days}d", interval="1d", progress=False)
    if df is None or df.empty:
        raise ValueError("no data")

    s = _get_close(df)  # ← 常に Series（1D）
    ret = s.pct_change()

    # ベンチ（失敗しても続行）
    try:
        bdf = yf.download(bench, period=f"{days}d", interval="1d", progress=False)
        if bdf is not None and not bdf.empty:
            b = _get_close(bdf)
            bret = b.pct_change()
        else:
            b, bret = None, None
    except Exception:
        b, bret = None, None

    # トレンド：回帰傾き（60日・年率）
    y = s.tail(60).to_numpy(dtype=float).ravel()  # ★必ず1Dにする
    x = np.arange(len(y), dtype=float)
    if len(y) < 2:
        slope_ann_pct = float("nan")
    else:
        k, _ = np.polyfit(x, y, 1)
        slope_ann_pct = float((k / y[-1]) * 100.0 * TRADING_DAYS)

    ma20  = float(s.rolling(20).mean().iloc[-1])  if len(s) >= 20  else None
    ma50  = float(s.rolling(50).mean().iloc[-1])  if len(s) >= 50  else None
    ma200 = float(s.rolling(200).mean().iloc[-1]) if len(s) >= 200 else None
    ma_stack = "bull" if (ma20 is not None and ma50 is not None and ma200 is not None and ma20 > ma50 > ma200) \
               else ("bear" if (ma20 is not None and ma50 is not None and ma200 is not None and ma20 < ma50 < ma200)
                     else "mixed")

    # ADX(14)
    # 必要列が揃っていないケースに備え、落ちないように作成
    try:
        h = df["High"]
        l = df["Low"]
        c = df["Close"] if "Close" in df.columns else (df["Adj Close"] if "Adj Close" in df.columns else _get_close(df))
        adx14 = _adx(pd.DataFrame({"High": h, "Low": l, "Close": c}).dropna()[["High","Low","Close"]])
    except Exception:
        adx14 = None

    # 相対強さ：6か月超過（126営業日）
    rs_6m = None
    try:
        if bret is not None and len(s) > 126:
            rs_6m = float((s.pct_change(126).iloc[-1] - (b.pct_change(126).iloc[-1] if b is not None else 0)) * 100)
    except Exception:
        rs_6m = None

    # 52週高値/安値乖離
    try:
        roll_max = s.rolling(252).max().iloc[-1]
        roll_min = s.rolling(252).min().iloc[-1]
        from_52w_high = float((s.iloc[-1] / roll_max - 1) * 100) if pd.notna(roll_max) and roll_max else None
        from_52w_low  = float((s.iloc[-1] / roll_min - 1) * 100) if pd.notna(roll_min) and roll_min else None
    except Exception:
        from_52w_high = from_52w_low = None

    # リスク・流動性
    vol20 = _ann_vol(ret.tail(20).dropna()) if ret.notna().sum() >= 20 else None
    vol60 = _ann_vol(ret.tail(60).dropna()) if ret.notna().sum() >= 60 else None

    try:
        vol = df["Volume"] if "Volume" in df.columns else None
        adv20 = float((s * vol).rolling(20).mean().iloc[-1]) if vol is not None else None
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
            "atr14": float(
                pd.concat([
                    (df["High"] - df["Low"]).abs(),
                    (df["High"] - df["Close"].shift()).abs() if "Close" in df.columns else (df["High"] - df["Adj Close"].shift()).abs(),
                    (df["Low"]  - df["Close"].shift()).abs() if "Close" in df.columns else (df["Low"]  - df["Adj Close"].shift()).abs(),
                ], axis=1).max(axis=1)
                .rolling(14).mean().iloc[-1]
            ) if {"High","Low"}.issubset(df.columns) and (("Close" in df.columns) or ("Adj Close" in df.columns)) else None
        },
        "liquidity": {
            "adv20": adv20
        }
    }