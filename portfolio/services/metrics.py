# portfolio/services/metrics.py
from __future__ import annotations
import numpy as np
import pandas as pd
import yfinance as yf

TRADING_DAYS = 252

def _get_close(df: pd.DataFrame) -> pd.Series:
    """yfinanceの列ゆらぎを吸収してClose系列を返す（auto_adjust=True想定）"""
    if "Close" in df.columns:
        s = df["Close"]
    elif "Adj Close" in df.columns:
        s = df["Adj Close"]
    else:
        # マルチインデックス対策
        for c in df.columns:
            if isinstance(c, tuple) and c[0] in ("Close", "Adj Close"):
                s = df[c]
                break
        else:
            raise KeyError("Close")
    return s.dropna()

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

def _decide(payload: dict) -> dict:
    """
    ① “買い/見送り”の軽量ルール判定
      - BUY 条件（どちらかを満たす）
        A) MA整列 bull AND 回帰傾き60d年率 > 10 AND ADX14 > 15
        B) 回帰傾き60d年率 > 20 AND 52週高値までの乖離 > -10%
      - AVOID 条件（どれか）
        price < MA200 OR ADX14 < 12 OR RS6M <= 0
      - それ以外は WATCH
    """
    t = payload.get("trend", {})
    r = payload.get("relative", {})
    risk = payload.get("risk", {})

    price = payload.get("price")
    ma = t.get("ma", {}) if t else {}
    ma200 = ma.get("200")
    stack = ma.get("stack")
    slope = t.get("slope_ann_pct_60")
    adx14 = t.get("adx14")
    rs6 = r.get("rs_6m_pct")
    from_hi = r.get("from_52w_high_pct")

    reasons = []

    # AVOID
    avoid = False
    if price is not None and ma200 is not None and price < ma200:
        avoid = True; reasons.append("価格がMA200未満")
    if adx14 is not None and adx14 < 12:
        avoid = True; reasons.append("ADXが弱い(<12)")
    if rs6 is not None and rs6 <= 0:
        avoid = True; reasons.append("指数アンダーパフォーム(RS6M≤0)")
    if avoid:
        return {
            "label": "AVOID",
            "reasons": reasons,
            "rules": {
                "price_lt_ma200": price is not None and ma200 is not None and price < ma200,
                "adx_weak": adx14 is not None and adx14 < 12,
                "rs6m_nonpos": rs6 is not None and rs6 <= 0,
            }
        }

    # BUY
    condA = (stack == "bull") and (slope is not None and slope > 10) and (adx14 is not None and adx14 > 15)
    condB = (slope is not None and slope > 20) and (from_hi is not None and from_hi > -10)
    if condA:
        reasons.append("MA整列bull & 傾き>10% & ADX>15")
    if condB:
        reasons.append("傾き>20% & 52週高値まで-10%以内")
    if condA or condB:
        return {
            "label": "BUY",
            "reasons": reasons or ["買い条件を満たす"],
            "rules": {"A": condA, "B": condB}
        }

    # WATCH
    return {
        "label": "WATCH",
        "reasons": ["条件未充足（観察）"],
        "rules": {}
    }

def get_metrics(ticker: str, bench: str = "^TOPX", days: int = 420) -> dict:
    # 価格
    df = yf.download(ticker, period=f"{days}d", interval="1d", progress=False)
    if df is None or df.empty:
        raise ValueError("no data")

    s = _get_close(df)
    ret = s.pct_change()
    price = float(s.iloc[-1])

    # ベンチ
    try:
        bdf = yf.download(bench, period=f"{days}d", interval="1d", progress=False)
        b = _get_close(bdf)
        bret = b.pct_change()
    except Exception:
        b = None
        bret = None

    # トレンド：回帰傾き（60日・年率）
    y = s.tail(60).values.astype(float)
    x = np.arange(len(y), dtype=float)
    k, _ = np.polyfit(x, y, 1)
    slope_ann_pct = float((k / y[-1]) * 100.0 * TRADING_DAYS)

    ma20 = float(s.rolling(20).mean().iloc[-1])
    ma50 = float(s.rolling(50).mean().iloc[-1])
    ma200 = float(s.rolling(200).mean().iloc[-1])
    ma_stack = "bull" if ma20 > ma50 > ma200 else ("bear" if ma20 < ma50 < ma200 else "mixed")

    # ADX(14)
    adx14 = _adx(df.dropna()[["High","Low","Close"]])

    # 相対強さ：6か月超過（126営業日）
    rs_6m = None
    if bret is not None and len(s) > 126 and b is not None and len(b) > 126:
        rs_6m = float((s.pct_change(126).iloc[-1] - b.pct_change(126).iloc[-1]) * 100)

    # 52週高値/安値乖離
    roll_max = s.rolling(252).max().iloc[-1]
    roll_min = s.rolling(252).min().iloc[-1]
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

    payload = {
        "ok": True,
        "asof": str(s.index[-1].date()),
        "price": price,
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

    # ①意思決定の付与
    payload["decision"] = _decide(payload)
    return payload